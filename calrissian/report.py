import logging
import yaml
from datetime import datetime
import threading

log = logging.getLogger("calrissian.report")

SECONDS_PER_HOUR = 60.0 * 60.0


class TimedReport(object):
    """
    Report on operations with a specific start time and finish time.
    """

    def __init__(self, start_time=None, finish_time=None):
        self.start_time = start_time
        self.finish_time = finish_time

    def start(self, start_time=None):
        self.start_time = start_time if start_time else datetime.now()

    def finish(self, finish_time=None):
        self.finish_time = finish_time if finish_time else datetime.now()

    def elapsed_seconds(self):
        delta = self.finish_time - self.start_time
        total_seconds = delta.total_seconds()
        if total_seconds < 0:
            raise ValueError('Negative time is not allowed: {}'.format(total_seconds))
        else:
            return total_seconds

    def elapsed_hours(self):
        return self.elapsed_seconds() / SECONDS_PER_HOUR


class ResourceParser(object):
    """
    Base class for converting Kubernetes resources (memory/CPU) from strings to reportable numbers
    """
    kind = None
    url = None
    suffixes = None

    @classmethod
    def parse(cls, value):
        try:
            for suffix, factor in cls.suffixes.items():
                if value.endswith(suffix):
                    return float(value.replace(suffix, '')) * factor
            # No suffix, assume raw number
            return float(value)
        except Exception:
            raise ValueError('Unable to parse \'{}\' as {}. See {}'.format(value, cls.kind, cls.url))


class MemoryParser(ResourceParser):
    """
    Converts Kubernetes memory resource strings (e.g. 1Mi, 1G)to byte quantities
    """
    kind = 'memory'
    url = 'https://kubernetes.io/docs/concepts/configuration/manage-compute-resources-container/#meaning-of-memory'
    suffixes = {
        'E': 1e18,
        'P': 1e15,
        'T': 1e12,
        'G': 1e9,
        'M': 1e6,
        'K': 1e3,
        'Ei': 2**60,
        'Pi': 2**50,
        'Ti': 2**40,
        'Gi': 2**30,
        'Mi': 2**20,
        'Ki': 2**10,
    }

    @classmethod
    def parse_to_megabytes(cls, value):
        return cls.parse(value) / 1024


class CPUParser(ResourceParser):
    """
    Converts Kubernetes CPU resource strings (e.g. 2, 200m) to floating point CPU quantities.
    """

    kind = 'cpu'
    url = 'https://kubernetes.io/docs/concepts/configuration/manage-compute-resources-container/#meaning-of-cpu'
    suffixes = {
        'm': 0.001
    }


class TimedResourceReport(TimedReport):
    """
    Adds CPU and memory usage to TimedReport, in order to calculate resource
    usage over the duration of the timed report
    """
    def __init__(self, cpus=0, ram_megabytes=0, *args, **kwargs):
        self.cpus = cpus
        self.ram_megabytes = ram_megabytes
        super(TimedResourceReport, self).__init__(*args, **kwargs)

    def ram_megabyte_hours(self):
        return self.ram_megabytes * self.elapsed_hours()

    def cpu_hours(self):
        return self.cpus * self.elapsed_hours()

    @classmethod
    def from_completion_result(cls, result):
        cpus = CPUParser.parse(result.cpus)
        ram_megabytes = MemoryParser.parse_to_megabytes(result.memory)
        return cls(start_time=result.start_time, finish_time=result.finish_time, cpus=cpus, ram_megabytes=ram_megabytes)


class Event(object):
    """
    Represents a start or finish event in a report, associated with its time.
    Event objects are intended to be sorted into a list and processed
    """

    # These numeric values are used to sort finish events before start events
    # if the Event.time is identical
    START = 1
    FINISH = -1

    def __init__(self, time, type, report):
        self.time = time
        self.type = type
        self.report = report

    @classmethod
    def start_event(cls, report):
        """
        Generate a start event for the provided report at its start time
        :param report:
        :return:
        """
        return Event(report.start_time, Event.START, report)

    @classmethod
    def finish_event(cls, report):
        """
        Generate a finish event for the provided report at its finish time
        :param report: a TimedResourceReport
        :return: an Event
        """
        return Event(report.finish_time, Event.FINISH, report)

    def process(self, processor):
        """
        Call the processor's process method with this Event's report and type
        :param processor: Object that implements .process(report, type)
        :return: None
        """
        processor.process(self.report, self.type)


class MaxParallelCountProcessor(object):
    """
    Simple processor to track the maximum parallel reports
    The process() method add count_unit (1) to self.count
    for each START and subtracts it for each FINISH, and recomputes self.max on each iteration
    """

    def __init__(self):
        self.count = 0
        self.max = 0

    def count_unit(self, report):
        """
        The unit to count when processing an event. Override this based on the report to calculate different
        max parallel metrics
        :param report: Report for context (not used in base class)
        :return: The value to count (here, 1)
        """
        return 1

    def process(self, report, event_type):
        """
        Examine the event type, add/subtract the count unit, and recompute max
        :param report: The report to consider
        :param event_type: Event.START or Event.FINISH
        :return: None
        """
        if event_type == Event.START:
            self.count += self.count_unit(report)
        elif event_type == Event.FINISH:
            self.count -= self.count_unit(report)
        self.max = max(self.max, self.count)

    def result(self):
        return self.max


class MaxParallelCPUsProcessor(MaxParallelCountProcessor):
    """
    Subclass of MaxParallelCountProcessor that counts cpus
    """

    def count_unit(self, report):
        return report.cpus


class MaxParallelRAMProcessor(MaxParallelCountProcessor):
    """
    Subclass of MaxParallelCountProcessor that counts ram_megabytes
    """

    def count_unit(self, report):
        return report.ram_megabytes


class TimelineReport(TimedReport):
    """
    A TimedReport that contains children.
    Can calculate totals and parallel statistics
    Automatically computes start_time and finish_time based on earliest/latest child reports
    """

    def __init__(self, *args, **kwargs):
        self.children = []
        super(TimelineReport, self).__init__(*args, **kwargs)

    def add_report(self, report):
        self.children.append(report)
        self._recalculate_times()

    def total_cpu_hours(self):
        return sum([child.cpu_hours() for child in self.children])

    def total_ram_megabyte_hours(self):
        return sum([child.ram_megabyte_hours() for child in self.children])

    def total_tasks(self):
        return len(self.children)

    def max_parallel_tasks(self):
        processor = MaxParallelCountProcessor()
        self._walk(processor)
        return processor.result()

    def max_parallel_cpus(self):
        processor = MaxParallelCPUsProcessor()
        self._walk(processor)
        return processor.result()

    def max_parallel_ram_megabytes(self):
        processor = MaxParallelRAMProcessor()
        self._walk(processor)
        return processor.result()

    def _recalculate_times(self):
        start_times = [c.start_time for c in self.children if c.start_time]
        if start_times:
            self.start_time = sorted(start_times)[0]
        finish_times = [c.finish_time for c in self.children if c.finish_time]
        if finish_times:
            self.finish_time = sorted(finish_times)[-1]

    def _walk(self, processor):
        events = []
        for report in self.children:
            events.append(Event.start_event(report))
            events.append(Event.finish_event(report))
        # Sort the events by their time and type, putting finishes ahead of starts when simultaneous.
        for event in sorted(events, key=lambda x: (x.time, x.type,)):
            event.process(processor)
        return processor.result()

    def to_yaml(self):
        result = vars(self)
        result['children'] = [vars(x) for x in self.children]
        return yaml.safe_dump(result)


class Reporter(object):
    """
    Singleton thread-safe reporting class
    """
    timeline_report = TimelineReport()
    lock = threading.Lock()

    def __enter__(self):
        Reporter.lock.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        Reporter.lock.release()

    def add_report(self, report):
        Reporter.timeline_report.add_report(report)

    @staticmethod
    def clear():
        with Reporter():
            Reporter.timeline_report = TimelineReport()

    @staticmethod
    def get_report():
        with Reporter():
            return Reporter.timeline_report


def write_report(filename):
    with open(filename, 'w') as file:
        file.write(Reporter.get_report().to_yaml())