import re
import time
import functools
from threading import Thread, Lock

from svgelements import Color

STATE_UNKNOWN = -1
STATE_INITIALIZE = 0
STATE_IDLE = 1
STATE_ACTIVE = 2
STATE_BUSY = 3
STATE_PAUSE = 4
STATE_END = 5
STATE_WAIT = 7  # Controller is waiting for something. This could be aborted.
STATE_TERMINATE = 10


class Modifier:
    """
    A modifier alters a context with some additional functionality set during attachment and detachment.

    These are also booted and shutdown with the kernel. The modifications to the kernel are not expected to be undone.
    Rather the detach should kill any secondary processes the modifier may possess.

    At detach the assumption is that the Modifier's ecosystem is the same.

    """

    def __init__(self, context, name=None, channel=None):
        self.context = context
        self.name = name
        self.state = STATE_INITIALIZE

    def boot(self, *args, **kwargs):
        """
        Called when the context the modifier attached to is booted. This is typical for devices.
        """
        pass

    def attach(self, *args, **kwargs):
        """
        Called by activate to attach module to device.

        This should be overloaded when making a specific module type for reuse, where using init would require specific
        subclassing, or for modules that are intended to expand the functionality of a device rather than compliment
        that functionality.
        """
        pass

    def detach(self, *args, **kwargs):
        """
        Called by deactivate to detach the module from device.

        Devices are not expected to undo all changes they made. This would typically only happen on the closing of a
        particular context.

        :param device:
        :param channel:
        :return:
        """
        pass


class Module:
    """
    Modules are a generic lifecycle object. These are registered in the kernel as modules and when open() is called for
    a context. When close() is called on the context, it will close and delete references to the opened module and call
    finalize().

    If an opened module is tries to open() a second time in a context and it was never closed. The device restore()
    function is called for the device, with the same args and kwargs that would have been called on __init__().
    """

    def __init__(self, context, name=None, *args, **kwargs):
        self.context = context
        self.name = name
        self.state = STATE_INITIALIZE

    def initialize(self, *args, **kwargs):
        """Initialize() is called after open() to setup the module and allow it to register various hooks into the
        kernelspace."""
        pass

    def restore(self, *args, **kwargs):
        """Called with the same values of __init()__ on an attempted reopen."""
        pass

    def finalize(self, *args, **kwargs):
        """Finalize is called after close() to unhook various kernelspace hooks. This will happen if kernel is being
        shutdown or if this individual module is being closed on its own."""
        pass


def console_option(name, short=None, **kwargs):
    def decor(func):
        kwargs['name'] = name
        kwargs['short'] = short
        func.options.insert(0, kwargs)
        return func
    return decor


def console_argument(name, **kwargs):
    def decor(func):
        kwargs['name'] = name
        func.arguments.insert(0, kwargs)
        return func
    return decor


_cmd_parse = [
    ('OPT', r'-([a-zA-Z]+)'),
    ('LONG', r'--([^ ,\t\n\x09\x0A\x0C\x0D]+)'),
    ('PARAM', r'([^ ,\t\n\x09\x0A\x0C\x0D]+)'),
    ('SKIP', r'[ ,\t\n\x09\x0A\x0C\x0D]+')
]
_CMD_RE = re.compile('|'.join('(?P<%s>%s)' % pair for pair in _cmd_parse))


def _cmd_parser(text):
    pos = 0
    limit = len(text)
    while pos < limit:
        match = _CMD_RE.match(text, pos)
        if match is None:
            break  # No more matches.
        kind = match.lastgroup
        start = pos
        pos = match.end()
        if kind == 'SKIP':
            continue
        if kind == 'PARAM':
            value = match.group()
            yield kind, value, start, pos
        elif kind == 'LONG':
            value = match.group()
            yield kind, value[2:], start, pos
        elif kind == 'OPT':
            value = match.group()
            for letter in value[1:]:
                yield kind, letter, start, start+1
                start += 1


def console_command(context, path=None, regex=False, hidden=False, help=None, data_type=None):

    def decorator(func):

        @functools.wraps(func)
        def inner(command, remainder, channel, **ik):
            if remainder is None or len(remainder) == 0:
                value = func(command, remainder=remainder, channel=channel, **ik)
                return value, remainder
            options = inner.options
            arguments = inner.arguments
            stack = list()
            stack.extend(arguments)
            kwargs = dict()
            argument_index = 0
            opt_index = 0
            pos = 0
            for kind, value, start, pos in _cmd_parser(remainder):
                if kind == 'PARAM':
                    if argument_index == len(stack):
                        pos = start
                        break  # Nothing else is expected.
                    k = stack[argument_index]
                    argument_index += 1
                    if 'type' in k and value is not None:
                        value = k['type'](value)
                    key = k['name']
                    current = kwargs.get(key, True)
                    if current is True:
                        kwargs[key] = [value]
                    else:
                        kwargs[key].append(value)
                    opt_index = argument_index
                elif kind == 'LONG':
                    for pk in options:
                        if value == pk['name']:
                            if pk.get('action') != 'store_true':
                                count = pk.get('nargs', 1)
                                for i in range(count):
                                    stack.insert(opt_index, pk)
                                    opt_index += 1
                            kwargs[value] = True
                            break
                    opt_index = argument_index
                elif kind == 'OPT':
                    for pk in options:
                        if value == pk['short']:
                            if pk.get('action') != 'store_true':
                                stack.insert(opt_index, pk)
                                opt_index += 1
                            kwargs[pk['name']] = True
                            break

            # Any singleton list arguments should become their only element.
            for i in range(argument_index):
                k = stack[i]
                key = k['name']
                current = kwargs.get(key)
                if isinstance(current, list):
                    if len(current) == 1:
                        kwargs[key] = current[0]

            # Any unprocessed positional arguments get default values.
            for i in range(argument_index, len(stack)):
                k = stack[i]
                value = k.get('default')
                if 'type' in k and value is not None:
                    value = k['type'](value)
                key = k['name']
                current = kwargs.get(key)
                if current is None:
                    kwargs[key] = [value]
                else:
                    kwargs[key].append(value)
            remainder = remainder[pos:]
            if len(remainder) > 0:
                kwargs['remainder'] = remainder
                kwargs['args'] = remainder.split()
            value = func(command, channel=channel, **ik, **kwargs)
            return value, remainder

        # Main Decorator
        try:
            kernel = context._kernel
            subpath = context._path
        except AttributeError:
            kernel = context
            subpath = ''
        cmd = 'command'
        cmd_path = "%s/%s" % (subpath, cmd)
        while cmd_path.startswith('/'):
            cmd_path = cmd_path[1:]
        if isinstance(path, tuple):
            for subitem in path:
                p = '%s/%s' % (cmd_path, subitem)
                kernel.register(p, inner)
        else:
            p = '%s/%s' % (cmd_path, path)
            kernel.register(p, inner)
        inner.long_help = func.__doc__
        inner.help = help
        inner.regex = regex
        inner.hidden = hidden
        inner.data_type = data_type if data_type is not None else type(None)
        inner.arguments = list()
        inner.options = list()
        return inner

    return decorator


class Context:
    """
    Contexts serve as path relevant snapshots of the kernel. These are are the primary interaction between the modules
    and the kernel. They permit getting other contexts of the kernel as well. This should serve as the primary interface
    code between the kernel and the modules.
    """

    def __init__(self, kernel, path):
        self._kernel = kernel
        self._path = path
        self._state = STATE_UNKNOWN
        self.opened = {}
        self.attached = {}

    def __str__(self):
        return "Context('%s')" % self._path

    def boot(self, channel=None):
        """
        Boot calls all attached modifiers with the boot command.

        :param channel:
        :return:
        """
        for attached_name in self.attached:
            attached = self.attached[attached_name]
            attached.boot(channel=channel)

    def abs_path(self, subpath):
        """
        The absolute path function determines the absolute path of the given subpath within the current path of the
        context.

        :param subpath: relative path to the path at this context
        :return:
        """
        subpath = str(subpath)
        if subpath.startswith('/'):
            return subpath[1:]
        if self._path is None or self._path == '/':
            return subpath
        return "%s/%s" % (self._path, subpath)

    def derive(self, path):
        """
        Derive a subpath context.

        :param path:
        :return:
        """
        return self._kernel.get_context(self.abs_path(path))

    def get_context(self, path):
        """
        Get a context at a given path location.

        :param path: path location to get a context.
        :return:
        """
        return self._kernel.get_context(path)

    def derivable(self):
        """
        Generate all sub derived paths.

        :return:
        """
        for e in self._kernel.derivable(self._path):
            yield e

    def setting(self, setting_type, key, default=None):
        """
        Registers a setting to be used between modules.

        If the setting exists, its value remains unchanged.
        If the setting exists in the persistent storage that value is used.
        If there is no settings value, the default will be used.

        :param setting_type: int, float, str, or bool value
        :param key: name of the setting
        :param default: default value for the setting to have.
        :return: load_value
        """
        if hasattr(self, key) and getattr(self, key) is not None:
            return getattr(self, key)

        # Key is not located in the attr. Load the value.
        if not key.startswith('_'):
            load_value = self._kernel.read_persistent(setting_type, self.abs_path(key), default)
        else:
            load_value = default
        setattr(self, key, load_value)
        return load_value

    def flush(self):
        """
        Commit any and all values currently stored as attr for this object to persistent storage.
        """
        for attr in dir(self):
            if attr.startswith('_'):
                continue
            value = getattr(self, attr)
            if value is None:
                continue
            from svgelements import Color
            if isinstance(value, (int, bool, str, float, Color)):
                self._kernel.write_persistent(self.abs_path(attr), value)

    def execute(self, control):
        """
        Execute the given control code relative to the path of this context.

        :param control: Function to execute relative to the current position.
        :return:
        """
        try:
            funct = self._kernel.registered[self.abs_path("control/%s" % control)]
        except KeyError:
            return
        funct()

    def register(self, path, obj):
        """
        Register a object at a relative path to the current location.

        :param path: Path postion within this context to register an object.
        :param obj: Object to register.
        :return:
        """
        self._kernel.register(self.abs_path(path), obj)

    @property
    def registered(self):
        return self._kernel.registered

    @property
    def active(self):
        return self._kernel.active

    @property
    def contexts(self):
        return self._kernel.contexts

    def match(self, matchtext, suffix=False):
        """
        Delegate of Kernel match.
        :param matchtext:  regex matchtext to locate.
        :yield: matched entries.
        """
        for m in self._kernel.match(matchtext):
            if suffix:
                yield list(m.split('/'))[-1]
            else:
                yield m

    def find(self, path):
        """
        Finds a loaded instance. Or returns None if not such instance.

        Note: 'name' is not necessarily the type of instance. It could be the named value of the instance.

        :param path: The opened path to find the given instance.
        :return: The instance, if found, otherwise None.
        """
        try:
            return self.opened[path]
        except KeyError:
            return None

    def open(self, registered_path, *args, **kwargs):
        """
        Opens a registered module with the same instance path as the registered path. This is fairly standard but should
        not be used if the goal would be to open the same module several times.

        :param registered_path: registered path of the given module.
        :param args: args to open the module with.
        :param kwargs: kwargs to open the module with.
        :return:
        """
        return self.open_as(registered_path, registered_path, *args, **kwargs)

    def open_as(self, registered_path, instance_path, *args, **kwargs):
        """
        Opens a registered module. If that module already exists it returns the already open module.

        Instance_name is the name under which this given module is opened.

        If the module already exists, the restore function is called on that object, if restore() exists, with the same
        args and kwargs that were intended for the init() routine.

        :param registered_path: path of object being opened.
        :param instance_path: path of object should be attached.
        :param args: Args to pass to newly opened module.
        :param kwargs: Kwargs to pass to newly opened module.
        :return: Opened module.
        """
        try:
            find = self.opened[instance_path]
            try:
                find.restore(*args, **kwargs)
            except AttributeError:
                pass
            return find
        except KeyError:
            pass

        try:
            open_object = self._kernel.registered[registered_path]
        except KeyError:
            raise ValueError

        instance = open_object(self, instance_path, *args, **kwargs)
        channel = self._kernel.channel('open')
        instance.initialize(channel=channel)

        self.opened[instance_path] = instance
        return instance

    def close(self, instance_path, *args, **kwargs):
        """
        Closes an opened instance. Located at the instance_path location.

        This calls the close() function on the object (which may not exist). And calls finalize() on the module,
        which should exist.

        :param instance_path: Instance path to close.
        :return:
        """
        try:
            instance = self.opened[instance_path]
        except KeyError:
            return  # Nothing to close.
        try:
            del self.opened[instance_path]
        except KeyError:
            pass

        try:
            instance.close()
        except AttributeError:
            pass
        instance.finalize(*args, **kwargs)

    def activate(self, registered_path, *args, **kwargs):
        """
        Activates a modifier at this context. The activate calls and attaches a modifier located at the given path
        to be attached to this context.

        The modifier is opened and attached at the current context.

        :param registered_path: registered_path location of the modifier.
        :param args: arguments to call the modifier
        :param kwargs: kwargs to call the modifier
        :return: Modifier object.
        """
        try:
            find = self.attached[registered_path]
            try:
                find.restore(*args, **kwargs)
            except AttributeError:
                pass
            return find
        except KeyError:
            pass
        try:
            open_object = self._kernel.registered[registered_path]
        except KeyError:
            raise ValueError

        instance = open_object(self, registered_path, *args, **kwargs)
        self.attached[registered_path] = instance
        instance.attach(self, *args, **kwargs)
        return instance

    def deactivate(self, instance_path, *args, **kwargs):
        """
        Deactivate a modifier attached to this context.
        The detach() is called on the modifier and modifier is deleted from the list of attached.

        :param instance_path: Attached path location.
        :return:
        """
        try:
            instance = self.attached[instance_path]
            instance.detach(self, *args, **kwargs)
            del self.attached[instance_path]
        except (KeyError, AttributeError):
            pass

    def load_persistent_object(self, obj):
        """
        Loads values of the persistent attributes, at this context and assigns them to the provided object.

        The attribute type of the value depends on the provided object value default values.

        :param obj:
        :return:
        """
        for attr in dir(obj):
            if attr.startswith('_'):
                continue
            obj_value = getattr(obj, attr)

            from svgelements import Color
            if not isinstance(obj_value, (int, float, str, bool, Color)):
                continue
            load_value = self._kernel.read_persistent(type(obj_value), self.abs_path(attr))
            setattr(obj, attr, load_value)
            setattr(self, attr, load_value)

    def set_attrib_keys(self):
        """
        Iterate all the entries keys for the registered persistent settings, adds a None attribute for any key that
        exists.

        :return:
        """
        for k in self._kernel.keylist(self._path):
            if not hasattr(self, k):
                setattr(self, k, None)

    def signal(self, code, *message):
        """
        Signal delegate to the kernel.
        :param code: Code to delegate at this given context location.
        :param message: Message to send.
        :return:
        """
        self._kernel.signal(self.abs_path(code), *message)

    def last_signal(self, code):
        """
        Last Signal delegate to the kernel.

        :param code: Code to delegate at this given context location.
        :return: message value of the last signal sent for that code.
        """
        return self._kernel.last_signal(self.abs_path(code))

    def listen(self, signal, process):
        """
        Listen delegate to the kernel.

        :param signal: Signal code to listen for
        :param process: listener to be attached
        :return:
        """
        self._kernel.listen(self.abs_path(signal), process)

    def unlisten(self, signal, process):
        """
        Unlisten delegate to the kernel.

        :param signal: Signal to unlisten for.
        :param process: listener that is to be detached.
        :return:
        """
        self._kernel.unlisten(self.abs_path(signal), process)

    def channel(self, channel, *args, **kwargs):
        """
        Channel channel_open delegate to the kernel.

        :param channel: Channel to be opened.
        :param buffer: Buffer to be applied to the given channel and sent to any watcher upon connection.
        :return: Channel object that is opened.
        """
        return self._kernel.channel(self.abs_path(channel), *args, **kwargs)

    def console_function(self, data):
        return ConsoleFunction(self, data)

    def console(self, data):
        self._kernel.console(data)

    def schedule(self, job):
        self._kernel.schedule(job)

    def unschedule(self, job):
        self._kernel.unschedule(job)

    def threaded(self, func, thread_name=None):
        self._kernel.threaded(func, thread_name=thread_name)


class Kernel:
    """
    The Kernel serves as the central hub of communication between different objects within the system. These are mapped
    to particular contexts that have locations within the kernel. The contexts can have modules opened and modifiers
    applied to them. The kernel serves to store the location of registered objects, as well as providing a scheduler,
    signals, channels, and a command console to be used by the modules, modifiers, devices, and other objects.

    The Kernel stores a persistence object, thread interactions, contexts, a translation routine, a run_later operation,
    jobs for the scheduler, listeners for signals, channel information, a list of devices, registered commands.

    Devices are contexts with a device. These are expected to have a Spooler attached, and the path should consist
    of numbers.
    """

    def __init__(self, config=None):
        self.devices = {}
        self.active = None

        self.contexts = {}
        self.threads = {}
        self.registered = {}
        self.translation = lambda e: e
        self.run_later = lambda listener, message: listener(message)
        self.state = STATE_INITIALIZE
        self.jobs = {}

        self.thread = None

        self.signal_job = None
        self.listeners = {}
        self.adding_listeners = []
        self.removing_listeners = []
        self.last_message = {}
        self.queue_lock = Lock()
        self.message_queue = {}
        self._is_queue_processing = False

        self.channels = {}

        self.commands = []
        self.console_job = Job(job_name="kernel.console.ticks", process=self._console_job_tick, interval=0.05)
        self._console_buffer = ''
        self.queue = []
        self._console_channel = self.channel('console')
        self.console_channel_file = None

        if config is not None:
            self.set_config(config)
        else:
            self._config = None

    def __str__(self):
        return "Kernel()"

    def __setitem__(self, key, value):
        """
        Kernel value settings. If Config is registered this will be persistent.

        :param key: Key to set.
        :param value: Value to set
        :return: None
        """
        if isinstance(key, str):
            self.write_persistent(key, value)

    def __getitem__(self, item):
        """
        Kernel value get. If Config is set registered this will be persistent.

        As a shorthand any float, int, string, or bool set with this will also be found at kernel.item

        :param item:
        :return:
        """
        if isinstance(item, tuple):
            if len(item) == 2:
                t, key = item
                return self.read_persistent(t, key)
            else:
                t, key, default = item
                return self.read_persistent(t, key, default)
        return self.read_item_persistent(item)

    def _start_debugging(self):
        """
        Debug function hooks all functions within the device with a debug call that saves the data to the disk and
        prints that information.

        :return:
        """
        import functools
        import datetime
        import types
        filename = "MeerK40t-debug-{date:%Y-%m-%d_%H_%M_%S}.txt".format(date=datetime.datetime.now())
        debug_file = open(filename, "a")
        debug_file.write("\n\n\n")

        def debug(func, obj):
            @functools.wraps(func)
            def wrapper_debug(*args, **kwargs):
                args_repr = [repr(a) for a in args]

                kwargs_repr = ["%s=%s" % (k, v) for k, v in kwargs.items()]
                signature = ", ".join(args_repr + kwargs_repr)
                start = "Calling %s.%s(%s)" % (str(obj), func.__name__, signature)
                debug_file.write(start + '\n')
                print(start)
                t = time.time()
                value = func(*args, **kwargs)
                t = time.time() - t
                finish = "    %s returned %s after %fms" % (func.__name__, value, t * 1000)
                print(finish)
                debug_file.write(finish + '\n')
                debug_file.flush()
                return value

            return wrapper_debug

        attach_list = [modules for modules, module_name in self.opened.items()]
        attach_list.append(self)
        for obj in attach_list:
            for attr in dir(obj):
                if attr.startswith('_'):
                    continue
                fn = getattr(obj, attr)
                if not isinstance(fn, types.FunctionType) and \
                        not isinstance(fn, types.MethodType):
                    continue
                setattr(obj, attr, debug(fn, obj))

    # Lifecycle processes.

    def boot(self):
        """
        Kernel boot sequence. This should be called after all the registered devices are established.

        :return:
        """
        self.command_boot()
        self.thread = self.threaded(self.run, 'Scheduler')
        self.signal_job = self.add_job(run=self.delegate_messages, name='kernel.signals', interval=0.005)
        for context_name in list(self.contexts):
            context = self.contexts[context_name]
            try:
                context.boot()
            except AttributeError:
                pass
        self.set_active(None)
        for device in self.derivable('/'):
            try:
                d = int(device)
            except ValueError:
                # Devices are marked as integers.
                continue
            self.device_boot(d)

    def shutdown(self, channel=None):
        """
        Starts full shutdown procedure.

        Suspends all signals.
        Each initialized context is flushed and shutdown.
        Each opened module within the context is stopped and closed.
        Each attached modifier is shutdown and deactivate.

        All threads are stopped.

        Any residual attached listeners are made warnings.

        There's no way to unattach listeners without the signal process running in the scheduler.

        :param channel:
        :return:
        """
        _ = self.translation
        if channel is None:
            channel = self.get_context('/').channel('shutdown')

        self.state = STATE_END  # Terminates the Scheduler.

        self.set_active(None)  # Change active device to None
        self.process_queue()  # Notify listeners of state.

        # Close Modules
        for context_name in list(self.contexts):
            context = self.contexts[context_name]
            for opened_name in list(context.opened):
                obj = context.opened[opened_name]
                channel(_("%s: Finalizing Module %s: %s") % (str(context), opened_name, str(obj)))
                context.close(opened_name, channel=channel)

        # Detach Modifiers
        for context_name in list(self.contexts):
            context = self.contexts[context_name]

            for attached_name in list(context.attached):
                obj = context.attached[attached_name]
                channel(_("%s: Detaching %s: %s") % (str(context), attached_name, str(obj)))
                context.deactivate(attached_name, channel=channel)

        # Suspend Signals
        def signal(code, *message):
            channel(_("Suspended Signal: %s for %s" % (code, message)))
        self.signal = signal
        self.process_queue()  # Process last events.

        # Context Flush and Shutdown
        for context_name in list(self.contexts):
            context = self.contexts[context_name]
            channel(_("Saving Context State: '%s'") % str(context))
            context.flush()
            del self.contexts[context_name]
            channel(_("Context Shutdown Finished: '%s'") % str(context))
        channel(_("Shutting down."))

        # Stop/Wait for all threads
        thread_count = 0
        for thread_name in list(self.threads):
            thread_count += 1
            try:
                thread = self.threads[thread_name]
            except KeyError:
                channel(_("Thread %s exited safely") % (thread_name))
                continue

            if not thread.is_alive:
                channel(_("WARNING: Dead thread %s still registered to %s.") % (thread_name, str(thread)))
                continue

            channel(_("Finishing Thread %s for %s") % (thread_name, str(thread)))
            try:
                if thread is self.thread:
                    channel(_("%s is the current shutdown thread") % (thread_name))
                    continue
                channel(_("Asking thread to stop."))
                thread.stop()
            except AttributeError:
                pass
            channel(_("Waiting for thread %s: %s") % (thread_name, str(thread)))
            thread.join()
            channel(_("Thread %s has finished. %s") % (thread_name, str(thread)))
        if thread_count == 0:
            channel(_("No threads required halting."))

        for key, listener in self.listeners.items():
            if len(listener):
                if channel is not None:
                    channel(_("WARNING: Listener '%s' still registered to %s.") % (key, str(listener)))
        self.last_message = {}
        self.listeners = {}
        self.state = STATE_TERMINATE
        self.thread.join()
        channel(_("Shutdown."))

    # Device Lifecycle

    def device_boot(self, d, device_name=None, autoboot=True):
        """
        Device boot sequence. This is called on individual devices the kernel reads automatically the device_name and
        the  autoboot setting. If autoboot is set then the device is activated at the boot location. The context 'd'
        is activated with the correct device name registered in device/<device_name>

        :param d:
        :param device_name:
        :param autoboot:
        :return:
        """
        device_str = str(d)
        if device_str in self.devices:
            return self.devices[device_str]
        boot_device = self.get_context(device_str)
        boot_device.setting(str, 'device_name', device_name)
        boot_device.setting(bool, 'autoboot', autoboot)
        if boot_device.autoboot and boot_device.device_name is not None:
            boot_device.activate("device/%s" % boot_device.device_name)
            try:
                boot_device.boot()
            except AttributeError:
                pass
            self.devices[device_str] = boot_device
            self.set_active(boot_device)

    # Registration

    def match(self, matchtext):
        """
        Lists all registered paths that regex match the given matchtext

        :param matchtext: match text to match.
        :return:
        """
        match = re.compile(matchtext)
        for r in self.registered:
            if match.match(r):
                yield r

    def register(self, path, obj):
        """
        Register an element at a given subpath. If this Kernel is not root. Then
        it is registered relative to this location.

        :param path:
        :param obj:
        :return:
        """
        self.registered[path] = obj
        try:
            obj.sub_register(self)
        except AttributeError:
            pass

    # Persistent Object processing.

    def get_context(self, path):
        """
        Create a context derived from this kernel, at the given path.

        If this has been created previously, then return the previous object.

        :param path: path of context being gotten
        :return: Context object.
        """
        try:
            return self.contexts[path]
        except KeyError:
            pass
        derive = Context(self, path=path)
        self.contexts[path] = derive
        return derive

    def derivable(self, path):
        """
        Finds all derivable paths within the config from the set path location.
        :param path:
        :return:
        """
        if self._config is None:
            return
        self._config.SetPath(path)
        more, value, index = self._config.GetFirstGroup()
        while more:
            yield value
            more, value, index = self._config.GetNextGroup(index)
        self._config.SetPath('/')

    def read_item_persistent(self, key):
        """Directly read from persistent storage the value of an item."""
        if self._config is None:
            return None
        return self._config.Read(key)

    def read_persistent(self, t, key, default=None):
        """
        Directly read from persistent storage the value of an item.

        :param t: datatype.
        :param key: key used to reference item.
        :param default: default value if item does not exist.
        :return: value
        """
        if self._config is None:
            return default
        if default is not None:
            if t == str:
                return self._config.Read(key, default)
            elif t == int:
                return self._config.ReadInt(key, default)
            elif t == float:
                return self._config.ReadFloat(key, default)
            elif t == bool:
                return self._config.ReadBool(key, default)
            elif t == Color:
                return self._config.ReadInt(key, default)
        else:
            if t == str:
                return self._config.Read(key)
            elif t == int:
                return self._config.ReadInt(key)
            elif t == float:
                return self._config.ReadFloat(key)
            elif t == bool:
                return self._config.ReadBool(key)
            elif t == Color:
                return self._config.ReadInt(key)
        return default

    def write_persistent(self, key, value):
        """
        Directly write the value to persistent storage.

        :param key: The item key being read.
        :param value: the value of the item.
        """
        if self._config is None:
            return
        if isinstance(value, str):
            self._config.Write(key, value)
        elif isinstance(value, int):
            self._config.WriteInt(key, value)
        elif isinstance(value, float):
            self._config.WriteFloat(key, value)
        elif isinstance(value, bool):
            self._config.WriteBool(key, value)
        elif isinstance(value, Color):
            self._config.WriteInt(key, value)

    def clear_persistent(self, path):
        if self._config is None:
            return
        self._config.DeleteGroup(path)

    def delete_persistent(self, key):
        if self._config is None:
            return
        self._config.DeleteEntry(key)

    def load_persistent_string_dict(self, path, dictionary=None, suffix=False):
        if dictionary is None:
            dictionary = dict()
        for k in list(self.keylist(path)):
            item = self.read_item_persistent(k)
            if suffix:
                k = k.split('/')[-1]
            dictionary[k] = item
        return dictionary

    def keylist(self, path):
        """
        Get all keys located at the given path location. The keys are listed in absolute path locations.

        :param path:
        :return:
        """
        if self._config is None:
            return
        self._config.SetPath(path)
        more, value, index = self._config.GetFirstEntry()
        while more:
            yield "%s/%s" % (path, value)
            more, value, index = self._config.GetNextEntry(index)
        self._config.SetPath('/')

    def set_config(self, config):
        """
        Set the config object.

        :param config: Persistent storage object.
        :return:
        """
        if config is None:
            return
        self._config = config

    # Threads processing.

    def threaded(self, func, thread_name=None):
        """
        Register a thread, and run the provided function with the name if needed. When the function finishes this thread
        will exit, and deregister itself. During shutdown any active threads created will be told to stop and the kernel
        will wait until such time as it stops.

        :param func: The function to be executed.
        :param thread_name: The name under which the thread should be registered.
        :return: The thread object created.
        """
        if thread_name is None:
            thread_name = func.__name__
        thread = Thread(name=thread_name)

        def run():
            self.threads[thread_name] = thread
            try:
                func()
            except:
                import sys
                sys.excepthook(*sys.exc_info())
            del self.threads[thread_name]

        thread.run = run
        thread.start()
        return thread

    def set_active(self, active):
        """
        Changes the active context.

        :param active:
        :return:
        """
        old_active = self.active
        self.active = active
        if self.active is not old_active:
            self.signal('active', old_active, self.active)

    def get_text_thread_state(self, state):
        _ = self.translation
        if state == STATE_INITIALIZE:
            return _("Unstarted")
        elif state == STATE_TERMINATE:
            return _("Abort")
        elif state == STATE_END:
            return _("Finished")
        elif state == STATE_PAUSE:
            return _("Pause")
        elif state == STATE_BUSY:
            return _("Busy")
        elif state == STATE_WAIT:
            return _("Waiting")
        elif state == STATE_ACTIVE:
            return _("Active")
        elif state == STATE_IDLE:
            return _("Idle")
        elif state == STATE_UNKNOWN:
            return _("Unknown")

    # Scheduler processing.

    def run(self):
        """
        Scheduler main loop.

        Check the Scheduler thread state, and whether it should abort or pause.
        Check each job, and if that job is scheduled to run. Executes that job.
        :return:
        """
        self.state = STATE_ACTIVE
        while self.state != STATE_END:
            time.sleep(0.005)  # 200 ticks a second.
            if self.state == STATE_TERMINATE:
                break
            while self.state == STATE_PAUSE:
                # The scheduler is paused.
                time.sleep(0.1)
            if self.state == STATE_TERMINATE:
                break
            jobs = self.jobs
            for job_name in list(jobs):
                job = jobs[job_name]

                # Checking if jobs should run.
                if job.scheduled:
                    job._next_run = 0  # Set to zero while running.
                    if job.times is not None:
                        job.times = job.times - 1
                        if job.times <= 0:
                            del jobs[job_name]
                        if job.times < 0:
                            continue
                    try:
                        if isinstance(jobs, int):
                            job.process(job.args[0])
                        elif isinstance(job.args, tuple):
                            job.process(*job.args)
                        else:
                            job.process(job.args)
                    except:
                        import sys
                        sys.excepthook(*sys.exc_info())
                    job._last_run = time.time()
                    job._next_run += job._last_run + job.interval
        self.state = STATE_END

    def schedule(self, job):
        self.jobs[job.job_name] = job
        return job

    def unschedule(self, job):
        try:
            del self.jobs[job.job_name]
        except KeyError:
            pass # No such job.
        return job

    def add_job(self, run, name=None, args=(), interval=1.0, times=None):
        """
        Adds a job to the scheduler.

        :param run: function to run
        :param args: arguments to give to that function.
        :param interval: in seconds, how often should the job be run.
        :param times: limit on number of executions.
        :return: Reference to the job added.
        """
        job = Job(job_name=name, process=run, args=args, interval=interval, times=times)
        return self.schedule(job)

    def remove_job(self, job):
        return self.unschedule(job)

    def set_timer(self, command, name=None, times=1, interval=1.0):
        if name is None or len(name) == 0:
            i = 1
            while 'timer%d' % i in self.jobs:
                i += 1
            name = 'timer%d' % i
        if not name.startswith('timer'):
            name = 'timer' + name
        if times == 0:
            times = None
        self.schedule(ConsoleFunction(self.get_context('/'), command, interval=interval, times=times, job_name=name))

    # Signal processing.

    def signal(self, code, *message):
        """
        Signals add the latest message to the message queue.

        :param code: Signal code
        :param message: Message to send.
        """
        self.queue_lock.acquire(True)
        self.message_queue[code] = message
        self.queue_lock.release()

    def delegate_messages(self):
        """
        Delegate the process queue to the run_later thread.
        run_later should be a threading instance wherein all signals are delivered.
        """
        if self._is_queue_processing:
            return
        if self.run_later is not None:
            self.run_later(self.process_queue, None)
        else:
            self.process_queue(None)

    def process_queue(self, *args):
        """
        Performed in the run_later thread. Signal groups. Threadsafe.

        Process the signals queued up. Inserting any attaching listeners, removing any removing listeners. And
        providing the newly attached listeners the last message known from that signal.
        :param args: None
        :return:
        """
        if len(self.message_queue) == 0 and len(self.adding_listeners) == 0 and len(self.removing_listeners) == 0:
            return
        self._is_queue_processing = True
        add = None
        remove = None
        self.queue_lock.acquire(True)
        queue = self.message_queue
        if len(self.adding_listeners) != 0:
            add = self.adding_listeners
            self.adding_listeners = []
        if len(self.removing_listeners):
            remove = self.removing_listeners
            self.removing_listeners = []
        self.message_queue = {}
        self.queue_lock.release()
        if add is not None:
            for signal, funct in add:
                if signal in self.listeners:
                    listeners = self.listeners[signal]
                    listeners.append(funct)
                else:
                    self.listeners[signal] = [funct]
                if signal in self.last_message:
                    last_message = self.last_message[signal]
                    funct(*last_message)
        if remove is not None:
            for signal, funct in remove:
                if signal in self.listeners:
                    listeners = self.listeners[signal]
                    try:
                        listeners.remove(funct)
                    except ValueError:
                        print("Value error removing: %s  %s" % (str(listeners), signal))

        for code, message in queue.items():
            if code in self.listeners:
                listeners = self.listeners[code]
                for listener in listeners:
                    listener(*message)
            self.last_message[code] = message
        self._is_queue_processing = False

    def last_signal(self, code):
        """
        Queries the last signal for a particular code.
        :param code: code to query.
        :return: Last signal sent through the kernel for that code.
        """
        try:
            return self.last_message[code]
        except KeyError:
            return None

    def listen(self, signal, funct):
        self.queue_lock.acquire(True)
        self.adding_listeners.append((signal, funct))
        self.queue_lock.release()

    def unlisten(self, signal, funct):
        self.queue_lock.acquire(True)
        self.removing_listeners.append((signal, funct))
        self.queue_lock.release()

    # Channel processing.

    def channel(self, channel, *args, **kwargs):
        if channel not in self.channels:
            self.channels[channel] = Channel(channel, *args, **kwargs)
        return self.channels[channel]

    # Console Processing.

    def command_boot(self):
        _ = self.translation

        @console_command(self, ('help', '?'), hidden=True, help="help <help>")
        def help(command, channel, _, args=tuple(), **kwargs):
            if len(args) >= 1:
                extended_help = args[0]
                if self.active is not None:
                    for command_name in self.match('%s/command/%s' % (self.active._path, extended_help)):
                        command_func = self.registered[command_name]
                        channel(command_func.long_help)
                        return
                    for command_name in self.match('command/%s' % extended_help):
                        command_func = self.registered[command_name]
                        channel(command_func.long_help)
                        return
                channel(_("No extended help for: %s") % extended_help)
                return

            if self.active is not None:
                channel("--- %s Commands ---" % str(self.active))
                for command_name in self.match('%s/command/.*' % (self.active._path)):
                    command_func = self.registered[command_name]
                    help = command_func.help
                    if command_func.hidden:
                        continue
                    if help is not None:
                        channel('%s %s' % (command_name.split('/')[-1].ljust(15), help))
                    else:
                        channel(command_name.split('/')[-1])
                for command_re in self.match('%s/command_re/.*' % self.active._path):
                    command_func = self.registered[command_re]
                    help = command_func.help
                    if command_func.hidden:
                        continue
                    cmd_re = command_re.split('/')[-1]
                    if help is not None:
                        channel('%s %s' % (cmd_re.ljust(15), help))
                    else:
                        channel(cmd_re)
            channel("--- Global Commands ---")
            for command_name in self.match('command/.*'):
                command_func = self.registered[command_name]
                help = command_func.help
                if command_func.hidden:
                    continue
                if help is not None:
                    channel('%s %s' % (command_name.split('/')[-1].ljust(15), help))
                else:
                    channel(command_name.split('/')[-1])
            for command_re in self.match('command_re/.*'):
                command_func = self.registered[command_re]
                help = command_func.help
                if command_func.hidden:
                    continue
                cmd_re = command_re.split('/')[-1]
                if help is not None:
                    channel('%s %s' % (cmd_re.ljust(15), help))
                else:
                    channel(cmd_re)
            return

        @console_command(self, 'loop', help="loop <command>")
        def loop(command, channel, _, args=tuple(), **kwargs):
            self._tick_command(' '.join(args))

        @console_command(self, 'end', help="end <commmand>")
        def end(command, channel, _, args=tuple(), **kwargs):
            if len(args) == 0:
                self.commands.clear()
                self.schedule(self.console_job)
            else:
                self._untick_command(' '.join(args))

        @console_command(self, 'timer.*', regex=True, help="timer<?> <duration> <iterations>")
        def timer(command, channel, _, args=tuple(), **kwargs):
            name = command[5:]
            if len(args) == 0:
                channel(_('----------'))
                channel(_('Timers:'))
                i = 0
                for job_name in self.jobs:
                    if not job_name.startswith('timer'):
                        continue
                    i += 1
                    job = self.jobs[job_name]
                    parts = list()
                    parts.append('%d:' % i)
                    parts.append(job_name)
                    parts.append('"%s"' % str(job))
                    if job.times is None:
                        parts.append(_('forever,'))
                    else:
                        parts.append(_('%d times,') % job.times)
                    if job.interval is None:
                        parts.append(_('never'))
                    else:
                        parts.append(_('each %f seconds') % job.interval)
                    channel(' '.join(parts))
                channel(_('----------'))
                return
            if len(args) == 1:
                if args[0] == 'off':
                    if name == "*":
                        for job_name in list(self.jobs):
                            if not job_name.startswith('timer'):
                                continue
                            try:
                                job = self.jobs[job_name]
                            except KeyError:
                                continue
                            job.cancel()
                            self.unschedule(job)
                        channel(_("All timers canceled."))
                        return
                    try:
                        obj = self.jobs[command]
                        obj.cancel()
                        self.unschedule(obj)
                        channel(_("Timer %s canceled." % name))
                    except KeyError:
                        channel(_("Timer %s does not exist." % name))
                return
            if len(args) <= 3:
                channel(_("Syntax Error: timer<name> <times> <interval> <command>"))
                return
            try:
                timer_command = ' '.join(args[2:])
                self.set_timer(timer_command + '\n', name=name, times=int(args[0]), interval=float(args[1]))
            except ValueError:
                channel(_("Syntax Error: timer<name> <times> <interval> <command>"))
            return

        @console_command(self, 'register', help="register")
        def register(command, channel, _, args=tuple(), **kwargs):
            if len(args) == 0:
                channel(_('----------'))
                channel(_('Objects Registered:'))
                for i, name in enumerate(self.match('.*')):
                    obj = self.registered[name]
                    channel(_('%d: %s type of %s') % (i + 1, name, str(obj)))
                channel(_('----------'))
            if len(args) == 1:
                channel(_('----------'))
                channel('Objects Registered:')
                for i, name in enumerate(self.match('%s.*' % args[0])):
                    obj = self.registered[name]
                    channel('%d: %s type of %s' % (i + 1, name, str(obj)))
                channel(_('----------'))

        @console_command(self, 'context', help="context")
        def context(command, channel, _, args=tuple(), **kwargs):
            active_context = self.active
            if len(args) == 0:
                if active_context is not None:
                    channel("Active Context: %s" % str(active_context))
                for context_name in self.contexts:
                    yield context_name
            return

        @console_command(self, 'set', help="set [<key> <value>]")
        def set(command, channel, _, args=tuple(), **kwargs):
            active_context = self.active
            if len(args) == 0:
                for attr in dir(active_context):
                    v = getattr(active_context, attr)
                    if attr.startswith('_') or not isinstance(v, (int, float, str, bool)):
                        continue
                    channel('"%s" := %s' % (attr, str(v)))
                return
            if len(args) >= 2:
                attr = args[0]
                value = args[1]
                try:
                    if hasattr(active_context, attr):
                        v = getattr(active_context, attr)
                        if isinstance(v, bool):
                            if value == 'False' or value == 'false' or value == 0:
                                setattr(active_context, attr, False)
                            else:
                                setattr(active_context, attr, True)
                        elif isinstance(v, int):
                            setattr(active_context, attr, int(value))
                        elif isinstance(v, float):
                            setattr(active_context, attr, float(value))
                        elif isinstance(v, str):
                            setattr(active_context, attr, str(value))
                except RuntimeError:
                    channel(_('Attempt failed. Produced a runtime error.'))
                except ValueError:
                    channel(_('Attempt failed. Produced a value error.'))
            return

        @console_command(self, 'control', help="control [<executive>]")
        def control(command, channel, _, args=tuple(), **kwargs):
            active_context = self.active
            if len(args) == 0:
                for control_name in active_context.match('control'):
                    channel(control_name)
                for control_name in active_context.match('\d+/control'):
                    channel(control_name)
            else:
                control_name = ' '.join(args)
                controls = list(active_context.match('%s/control/.*' % active_context._path, True))
                if active_context is not None and control_name in controls:
                    active_context.execute(control_name)
                    channel(_("Executed '%s'") % control_name)
                elif control_name in list(active_context.match('control/.*', True)):
                    self.get_context('/').execute(control_name)
                    channel(_("Executed '%s'") % control_name)
                else:
                    channel(_("Control '%s' not found.") % control_name)
            return

        @console_command(self, 'module', help="module [(open|close) <module_name>]")
        def module(command, channel, _, args=tuple(), **kwargs):
            active_context = self.active
            if len(args) == 0:
                channel(_('----------'))
                channel(_('Modules Registered:'))
                for i, name in enumerate(self.match('module')):
                    channel('%d: %s' % (i + 1, name))
                channel(_('----------'))
                for i, name in enumerate(self.contexts):
                    context = self.contexts[name]
                    if len(context.opened) == 0:
                        continue
                    channel(_('Loaded Modules in Context %s:') % str(context._path))
                    for i, name in enumerate(context.opened):
                        module = context.opened[name]
                        channel(_('%d: %s as type of %s') % (i + 1, name, type(module)))
                    channel(_('----------'))
            else:
                value = args[0]
                if value == 'open':
                    index = args[1]
                    name = None
                    if len(args) >= 3:
                        name = args[2]
                    if index in self.registered:
                        if name is not None:
                            active_context.open_as(index, name)
                        else:
                            active_context.open(index)
                    else:
                        channel(_("Module '%s' not found.") % index)
                elif value == 'close':
                    index = args[1]
                    if index in active_context.opened:
                        active_context.close(index)
                    else:
                        channel(_("Module '%s' not found.") % index)
            return

        @console_command(self, 'modifier', help="modifier [(open|close) <module_name>]")
        def modifier(command, channel, _, args=tuple(), **kwargs):
            active_context = self.active
            if len(args) == 0:
                channel(_('----------'))
                channel(_('Modifiers Registered:'))
                for i, name in enumerate(self.match('modifier')):
                    channel('%d: %s' % (i + 1, name))
                channel(_('----------'))
                channel(_('Loaded Modifiers in Context %s:') % str(active_context._path))
                for i, name in enumerate(active_context.attached):
                    modifier = active_context.attached[name]
                    channel(_('%d: %s as type of %s') % (i + 1, name, type(modifier)))
                channel(_('----------'))
                channel(_('Loaded Modifiers in Device %s:') % str(active_context._path))
                for i, name in enumerate(active_context.attached):
                    modifier = active_context.attached[name]
                    channel(_('%d: %s as type of %s') % (i + 1, name, type(modifier)))
                channel(_('----------'))
            else:
                value = args[0]
                if value == 'open':
                    index = args[1]
                    if index in self.registered:
                        active_context.activate(index)
                    else:
                        channel(_("Modifier '%s' not found.") % index)
                elif value == 'close':
                    index = args[1]
                    if index in active_context.attached:
                        active_context.deactivate(index)
                    else:
                        channel(_("Modifier '%s' not found.") % index)
            return

        @console_command(self, 'schedule', help="schedule")
        def schedule(command, channel, _, args=tuple(), **kwargs):
            channel(_('----------'))
            channel(_('Scheduled Processes:'))
            for i, job_name in enumerate(self.jobs):
                job = self.jobs[job_name]
                parts = list()
                parts.append('%d:' % (i + 1))
                parts.append(str(job))
                if job.times is None:
                    parts.append(_('forever,'))
                else:
                    parts.append(_('%d times,') % job.times)
                if job.interval is None:
                    parts.append(_('never'))
                else:
                    parts.append(_('each %f seconds') % job.interval)
                channel(' '.join(parts))
            channel(_('----------'))
            return

        @console_command(self, 'channel', help="channel [(open|close|save) <channel_name>]")
        def channel(command, channel, _, args=tuple(), **kwargs):
            active_context = self.active
            if len(args) == 0:
                channel(_('----------'))
                channel(_('Channels Active:'))
                for i, name in enumerate(self.channels):
                    channel = self.channels[name]
                    if self._console_channel in channel.watchers:
                        is_watched = '* '
                    else:
                        is_watched = ''
                    channel('%s%d: %s' % (is_watched, i + 1, name))
            else:
                value = args[0]
                chan = args[1]
                if value == 'open':
                    if chan == 'console':
                        channel(_('Infinite Loop Error.'))
                    else:
                        active_context.channel(chan).watch(self._console_channel)
                        channel(_('Watching Channel: %s') % chan)
                elif value == 'close':
                    try:
                        active_context.channel(chan).unwatch(self._console_channel)
                        channel(_('No Longer Watching Channel: %s') % chan)
                    except KeyError:
                        channel(_('Channel %s is not opened.') % chan)
                elif value == 'save':
                    from datetime import datetime
                    if self.console_channel_file is None:
                        filename = "MeerK40t-channel-{date:%Y-%m-%d_%H_%M_%S}.txt".format(date=datetime.now())
                        channel(_('Opening file: %s') % filename)
                        self.console_channel_file = open(filename, "a")
                    channel(_('Recording Channel: %s') % chan)
                    active_context.channel(chan).watch(self._console_file_write)
            return

        @console_command(self, 'device', help="device [<value>]")
        def device(command, channel, _, args=tuple(), **kwargs):
            active_context = self.active
            if len(args) == 0:
                channel(_('----------'))
                channel(_('Backends permitted:'))
                for i, name in enumerate(self.match('device/')):
                    channel('%d: %s' % (i + 1, name))
                channel(_('----------'))
                channel(_('Existing Device:'))

                for device in list(active_context.derivable()):
                    try:
                        d = int(device)
                    except ValueError:
                        continue
                    try:
                        settings = active_context.derive(device)
                        device_name = settings.setting(str, 'device_name', 'Lhystudios')
                        autoboot = settings.setting(bool, 'autoboot', True)
                        channel(_('Device %d. "%s" -- Boots: %s') % (d, device_name, autoboot))
                    except ValueError:
                        break
                    except AttributeError:
                        break
                channel(_('----------'))
                channel(_('Devices Instances:'))
                try:
                    device_name = active_context.device_name
                except AttributeError:
                    device_name = "Unknown"

                try:
                    device_location = active_context.device_location
                except AttributeError:
                    device_location = "Unknown"
                for i, name in enumerate(self.devices):
                    device = self.devices[name]
                    try:
                        device_name = device.device_name
                    except AttributeError:
                        device_name = "Unknown"

                    try:
                        device_location = device.device_location
                    except AttributeError:
                        device_location = "Unknown"
                    channel(_('%d: %s on %s') % (i + 1, device_name, device_location))
                channel(_('----------'))
            else:
                value = args[0]
                try:
                    value = int(value)
                except ValueError:
                    value = None
                for i, name in enumerate(self.devices):
                    if i + 1 == value:
                        self.active = self.devices[name]
                        active_context.setting(str, 'device_location', 'Unknown')
                        channel(_('Device set: %s on %s') %
                              (active_context.device_name, active_context.device_location))
                        break
            return

        @console_command(self, 'flush', help="flush")
        def flush(command, channel, _, args=tuple(), **kwargs):
            active_context = self.active
            active_context.flush()
            channel(_('Persistent settings force saved.'))

        @console_command(self, 'shutdown', help="shutdown")
        def shutdown(command, channel, _, args=tuple(), **kwargs):
            if self.state not in (STATE_END, STATE_TERMINATE):
                self.shutdown()
            return

    def console(self, data):
        """
        Console accepts console data information. When a '\n' is seen
        it will execute that in the console_parser. This works like a
        terminal, where each letter of data can be sent to the console and
        execution will occur at the carriage return.

        :param data:
        :return:
        """
        if isinstance(data, bytes):
            data = data.decode()
        self._console_buffer += data
        while '\n' in self._console_buffer:
            pos = self._console_buffer.find('\n')
            command = self._console_buffer[0:pos].strip('\r')
            self._console_buffer = self._console_buffer[pos + 1:]
            self._console_parse(command, channel=self._console_channel)

    def _console_job_tick(self):
        """
        Processses the console_job ticks. This executes any outstanding queued commands and any looped commands.

        :return:
        """
        for command in self.commands:
            self._console_parse(command, channel=self._console_channel)
        if len(self.queue):
            for command in self.queue:
                self._console_parse(command, channel=self._console_channel)
            self.queue.clear()
        if len(self.commands) == 0 and len(self.queue) == 0:
            self.unschedule(self.console_job)

    def _console_queue(self, command):
        self.queue = [c for c in self.queue if c != command]  # Only allow 1 copy of any command.
        self.queue.append(command)
        if self.console_job not in self.jobs:
            self.add_job(self.console_job)

    def _tick_command(self, command):
        self.commands = [c for c in self.commands if c != command]  # Only allow 1 copy of any command.
        self.commands.append(command)
        if self.console_job not in self.jobs:
            self.schedule(self.console_job)

    def _untick_command(self, command):
        self.commands = [c for c in self.commands if c != command]
        if len(self.commands) == 0:
            self.unschedule(self.console_job)

    def _console_file_write(self, v):
        if self.console_channel_file is not None:
            self.console_channel_file.write('%s\r\n' % v)
            self.console_channel_file.flush()

    def _console_interface(self, command):
        pass

    def _console_parse(self, command, channel=None):
        """
        Console parse takes single line console commands.
        """
        # TODO: Make this a for loop to parse all remaining text as additional commands.
        # Silence Echo.
        if command.startswith('.'):
            command = command[1:]
        else:
            channel(command)

        # Divide command from remainder.
        pos = command.find(' ')
        if pos != -1:
            remainder = command[pos + 1:]
            command = command[0:pos]
        else:
            remainder = None

        # Set context based on command path. # TODO: This might need deprecating.
        _ = self.translation
        command = command.lower()
        if '/' in command:
            path = command.split('/')
            p = '/'.join(path[:-1])
            if len(p) == 0:
                p = '/'
            self.active = self.get_context(p)
            command = path[-1]

        data = None  # Initial data is null

        # Process all commands in the active_context
        paths = ['command/%s' % command.replace('+', '\+')]
        if self.active is not None:
            paths.insert(0, '%s/command/.*' % self.active._path)
        for match_text in paths:
            # Process command matches.
            for command_name in self.match(match_text):
                # TODO: Make sure +right works correctly. it doesn't
                command_funct = self.registered[command_name]
                cmd_re = command_name.split('/')[-1]
                if type(data) != command_funct.data_type:
                    continue  # We much match the input type.
                if command_funct.regex:
                    match = re.compile(cmd_re)
                    if not match.match(command):
                        continue
                else:
                    if cmd_re != command:
                        continue
                try:
                    data, remainder = command_funct(command, remainder, channel, data=data, _=_)
                except SyntaxError:
                    channel(_("Syntax Error: %s") % command_funct.help)
                except ValueError:
                    continue  # command match rejected.
                return


class Channel:
    def __init__(self, name, buffer_size=0, line_end=None):
        self.watchers = []
        self.greet = None
        self.name = name
        self.buffer_size = buffer_size
        self.line_end = line_end
        if buffer_size == 0:
            self.buffer = None
        else:
            self.buffer = list()

    def __call__(self, message, *args, **kwargs):
        if self.line_end is not None:
            message = message + self.line_end
        for w in self.watchers:
            w(message)
        if self.buffer is not None:
            self.buffer.append(message)
            if len(self.buffer) + 10 > self.buffer_size:
                self.buffer = self.buffer[-self.buffer_size:]

    def __len__(self):
        return self.buffer_size

    def __iadd__(self, other):
        self.watch(monitor_function=other)

    def __isub__(self, other):
        self.unwatch(monitor_function=other)

    def watch(self, monitor_function):
        for q in self.watchers:
            if q is monitor_function:
                return  # This is already being watched by that.
        self.watchers.append(monitor_function)
        if self.greet is not None:
            monitor_function(self.greet)
        if self.buffer is not None:
            for line in self.buffer:
                monitor_function(line)

    def unwatch(self, monitor_function):
        self.watchers.remove(monitor_function)


class Job:
    """
    Generic job for the scheduler.

    Jobs that can be scheduled in the scheduler-kernel to run at a particular time and a given number of times.
    This is done calling schedule() and unschedule() and setting the parameters for process, args, interval,
    and times. This is usually extended directly by a module requiring that functionality.
    """

    def __init__(self, process=None, args=(), interval=1.0, times=None, job_name=None):
        self.job_name = job_name
        self.state = STATE_INITIALIZE

        self.process = process
        self.args = args
        self.interval = interval
        self.times = times
        self._last_run = None
        self._next_run = time.time() + self.interval

    def __call__(self, *args, **kwargs):
        self.process(*args, **kwargs)

    def __str__(self):
        if self.job_name is not None:
            return self.job_name
        else:
            try:
                return self.process.__name__
            except AttributeError:
                return object.__str__(self)

    @property
    def scheduled(self):
        return self._next_run is not None and time.time() >= self._next_run

    def cancel(self):
        self.times = -1


class ConsoleFunction(Job):
    def __init__(self, context, data, interval=1.0, times=None, job_name=None):
        Job.__init__(self, self.__call__, None, interval, times, job_name)
        self.context = context
        self.data = data

    def __call__(self, *args, **kwargs):
        self.context.console(self.data)

    def __str__(self):
        return self.data.replace('\n', '')
