from CutCode import *

"""

The PlotPlanner simplifies the plotting modifications routines. These are buffered with plottable elements.
These can be submitted as destination graphics commands, or by submitting a plot routine. Which may yield either 2 or 
3 value coordinates. These are x, y, and on. Where on is a number between 0 and 1 which designates the on-value. In the
graphics commands the move is given a 0 and all other plots are given a 1. All graphics commands take an optional
on-value. If PPI is enabled, fractional on values are made non-fractional by carrying forward the value of on as a 
factor applied of the total value.
 
All plots are queued and processed in order. This queueing scheme is threadsafe, and should permit one thread reading
the plot values while another thread adds additional items to the queue. If the queue completely empties any processes
being applied to the plot stream are flushed prior to terminating the iterator.

Provided positions can be gapped or single, with adjacent or distant values. The on value is expected to denote whether
the transition from the current position to the new position should be drawn or not. Values that have an initial value
of zero will remain zero.

* Singles converts input into single positional shifts. This must be done to process the plot stream.
* PPI does pulses per inch carry forward with the given value.
* Dot Length requires any train of on-values must be of at least the proscribed length.
* Shift moves isolated single-on values to be adjacent to other on-values.
* Groups manipulates the output as max-length changeless orthogonal/diagonal positions.
"""


class PlotPlanner:
    def __init__(self, settings):
        # TODO: Plot planner should solve for initial directionality of new compact event.
        self.settings = settings
        self.group_enabled = True  # Required for Lhymicro-gl.

        self.flush = None
        self.jog = None
        self.current = None

        self.queue = []

        self.single_default = 1
        self.single_x = None
        self.single_y = None

        self.ppi_total = 0
        self.ppi = 1000.0

        self.dot_left = 0

        self.group_x = None
        self.group_y = None
        self.group_on = None
        self.group_dx = 0
        self.group_dy = 0

        self.shift_buffer = []
        self.shift_pixels = 0

    def gen(self):
        """
        Main method of generating the plot stream.

        :return:
        """
        while True:
            if self.flush is not None:  # First flush any buffer.
                # Buffer is flushing.
                for n in self.flush:
                    yield n
                self.flush = None

            if self.jog is not None:  # Perform any would-be jogs.
                sx, sy, on = self.jog
                self.single_x = sx
                self.single_y = sy
                self.group_x = sx
                self.group_y = sy
                self.jog = None
                yield sx, sy, on

            if self.current is not None:  # Process the current data.
                # Current is plotting
                for n in self.current:
                    yield n
                self.current = None

            if self.current is None:
                if len(self.queue) == 0:
                    for n in self.wrap(None):  # Process flush, and finish.
                        yield n
                    yield None, None, 8
                    return
                cut = self.queue.pop(0)
                self.current = self.wrap(cut.generator())  # Wrap current cut

                p_set = cut.settings
                s_set = self.settings

                self.settings = cut.settings

                start = cut.start()
                sx = int(start.x)
                sy = int(start.y)

                flush = False
                jog = 0
                if self.single_x != sx or self.single_y != sy:
                    # If this location is disjointed. We must flush and jog to the new location.
                    flush = True
                    jog |= 2  # new location required.
                if p_set is not s_set:
                    flush = True  # Laser Setting has changed, we must flush the buffer.
                    jog |= 4  # New settings required.
                if flush:
                    self.flush = self.wrap(None)  # Wrap flush.
                if jog:
                    self.jog = sx, sy, jog

    def push(self, plot):
        self.queue.append(plot)

    def wrap(self, plot):
        """
        Converts a series of inputs into a series of outputs. There is not a 1:1 input to output conversion.
        Processes can buffer data and return None. Processes are required to surrender any buffer they have if the
        given sequence ends with, or is None. This flushes out any data.

        If an input sequence still lacks a on-value then the single_default value will be utilized.
        Output sequences are iterables of x, y, on positions.

        :param plot: plottable element that should be wrapped
        :return: generator to produce plottable elements.
        """
        plot = self.single(plot)
        if self.settings.ppi_enabled:
            plot = self.apply_ppi(plot)
        if self.settings.shift_enabled:
            plot = self.shift(plot)
        if self.group_enabled:
            plot = self.group(plot)
        return plot

    def single(self, plot):
        """
        Convert a sequence set of positions into single unit plotted sequences.

        single_default sets the default for any unmarked processes.
        single_x sets the last known x position this routine has encountered.
        single_y sets the last known y position this routine has encountered.

        :param plot: plot generator
        :return:
        """
        if plot is None:
            yield None
        for event in plot:
            if len(event) == 3:
                x, y, on = event
            else:
                x, y = event
                on = self.single_default
            index = 1
            if self.single_x is None:
                self.single_x = x
                index = 0
            if self.single_y is None:
                self.single_y = y
                index = 0
            if x > self.single_x:
                dx = 1
            elif x < self.single_x:
                dx = -1
            else:
                dx = 0
            if y > self.single_y:
                dy = 1
            elif y < self.single_y:
                dy = -1
            else:
                dy = 0
            total_dx = x - self.single_x
            total_dy = y - self.single_y
            if total_dx == 0 and total_dy == 0:
                continue
            if total_dy * dx != total_dx * dy:
                raise ValueError("Must be uniformly diagonal or orthogonal: (%d, %d) is not." % (total_dx, total_dy))
            count = max(abs(total_dx), abs(total_dy)) + 1
            interpolated = [(self.single_x + (i * dx), self.single_y + (i * dy), on) for i in range(index, count)]
            self.single_x = x
            self.single_y = y
            for p in interpolated:
                yield p

    def apply_ppi(self, plot):
        """
        Converts single stepped plots, to apply PPI.

        Implements PPI power modulation.

        :param plot: generator of single stepped plots
        :return:
        """
        if plot is None:
            yield None
            return
        for event in plot:
            if event is None:
                yield None
                return
            x, y, on = event
            self.ppi_total += self.ppi * on
            if on and self.dot_left > 0:
                self.dot_left -= 1
                on = 1
            else:
                if self.ppi_total >= 1000.0:
                    on = 1
                    self.ppi_total -= 1000.0 * self.settings.dot_length
                    self.dot_left = self.settings.dot_length - 1
                else:
                    on = 0
                if on:
                    self.dot_left = self.settings.dot_length - 1
            yield x, y, on

    def shift(self, plot):
        """
        Tweaks on-values to simplify them into more coherent subsections.

        :param plot: generator of single stepped plots
        :return:
        """
        for event in plot:
            if event is None:
                while len(self.shift_buffer) > 0:
                    self.shift_pixels <<= 1
                    bx, by = self.shift_buffer.pop()
                    bon = (self.shift_pixels >> 3) & 1
                    yield bx, by, bon
                yield None
                return
            x, y, on = event
            self.shift_pixels <<= 1
            if on:
                self.shift_pixels |= 1
            self.shift_pixels &= 0b1111

            self.shift_buffer.insert(0, (x, y))
            if self.shift_pixels == 0b0101:
                self.shift_pixels = 0b0011
            elif self.shift_pixels == 0b1010:
                self.shift_pixels = 0b1100
            if len(self.shift_buffer) >= 4:
                bx, by = self.shift_buffer.pop()
                bon = (self.shift_pixels >> 3) & 1
                yield bx, by, bon

    def group(self, plot):
        """
        Converts a generated series of single stepped plots into grouped orthogonal/diagonal plots.

        :param plot: single stepped plots to be grouped into orth/diag sequences.
        :return:
        """
        for event in plot:
            if event is None:
                if self.group_x is not None and self.group_y is not None:
                    yield self.group_x, self.group_y, self.group_on
                self.group_dx = 0
                self.group_dy = 0
                return
            x, y, on = event
            if self.group_x is None:
                self.group_x = x
            if self.group_y is None:
                self.group_y = y
            if self.group_on is None:
                self.group_on = on
            if self.group_dx == 0 and self.group_dy == 0:
                self.group_dx = x - self.group_x
                self.group_dy = y - self.group_y
            if self.group_dx != 0 or self.group_dy != 0:
                if x == self.group_x + self.group_dx and y == self.group_y + self.group_dy and on == self.group_on:
                    # This is an orthogonal/diagonal step along the same path.
                    self.group_x = x
                    self.group_y = y
                    continue
                yield self.group_x, self.group_y, self.group_on
            self.group_dx = x - self.group_x
            self.group_dy = y - self.group_y
            if abs(self.group_dx) > 1 or abs(self.group_dy) > 1:
                # The last step was not valid.
                raise ValueError("dx(%d) or dy(%d) exceeds 1" % (self.group_dx, self.group_dy))
            self.group_x = x
            self.group_y = y
            self.group_on = on

    def clear(self):
        # TODO: Properly reset cut planner.
        self.queue.clear()