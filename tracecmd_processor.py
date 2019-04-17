from tracecmd import *
from traceprocessor import *

class EventCounts:

    def __init__(self):
        self.sched_switch = 0
        self.cpu_idle = 0
        self.update_cpu_metric = 0
        self.cpu_freq = 0
        self.binder_transaction = 0
        self.mali_utilization_stats = 0
        self.mali = 0
        self.temp = 0


class TracecmdProcessor:

    def __init__(self, filename):
        self.processed_events = []
        try:
            self.trace = Trace(filename)
        except Exception, e:
            print "Tracecmd file could not be read: %s" % str(e)
            sys.exit(1)

        self.event_count = EventCounts()
        self.processed_trace = self.process_trace()

    def process_trace(self):
        event = self.trace.read_next_event()
        self.handle_event(event)
        while event:
            event = self.trace.read_next_event()
            self.handle_event(event)

    def print_event_count(self):
        print "Total events: " + str(self.event_count.sched_switch + self.event_count.cpu_idle
                                     + self.event_count.sched_switch + self.event_count.binder_transaction +
                                     self.event_count.mali_utilization_stats + self.event_count.cpu_freq +
                                     self.event_count.mali)
        print "Sched switch: " + str(self.event_count.sched_switch)
        print "CPU idle: " + str(self.event_count.cpu_idle)
        print "Update CPU metric: " + str(self.event_count.update_cpu_metric)
        print "CPU freq: " + str(self.event_count.cpu_freq)
        print "Binder transactions: " + str(self.event_count.binder_transaction)
        print "Mali util: " + str(self.event_count.mali_utilization_stats)
        print "Mali: " + str(self.event_count.mali)
        print "Temp: " + str(self.event_count.temp)

    def handle_event(self, event):

        if not event:
            return

        if event.name == "sched_switch":
            self.event_count.sched_switch += 1

            prev_state = 'S'
            prev_state_int = event.num_field("prev_state")
            if prev_state_int == 1:
                prev_state = 'S'
            elif prev_state_int == 2:
                prev_state = 'D'
            elif prev_state_int == 4:
                prev_state = 'T'
            elif prev_state_int == 8:
                prev_state = 't'
            elif prev_state_int == 16:
                prev_state = 'Z'
            elif prev_state_int == 32:
                prev_state = 'X'
            elif prev_state_int == 64:
                prev_state = 'x'
            elif prev_state_int == 128:
                prev_state = 'K'
            elif prev_state_int == 256:
                prev_state = 'W'
            elif prev_state_int == 512:
                prev_state = 'P'

            next_pid = event.num_field("next_pid")
            self.processed_events.append(EventSchedSwitch(event.pid, event.ts / 1000,
                                event.cpu, event.name, prev_state, next_pid))
        elif event.name == "cpu_idle":
            self.event_count.cpu_idle += 1

            state = event.num_field("state")
            self.processed_events.append(EventIdle(event.ts / 1000, event.cpu, event.name,
                                state))
        elif event.name == "update_cpu_metric":
            self.event_count.update_cpu_metric += 1
            print "wait here"
        elif event.name == "cpu_freq":
            self.event_count.cpu_freq += 1

            CPU = event.num_field("cpu")
            freq = event.num_field("freq") * 1000
            self.processed_events.append(EventFreqChange(event.pid, event.ts / 1000,
                        event.cpu, freq, 0, CPU))

        elif event.name == "binder_transaction":
            self.event_count.binder_transaction += 1

            reply = event.num_field("reply")
            flags = event.num_field("flags")
            code = event.num_field("code")
            to_proc = event.num_field("to_thread")
            if to_proc == 0:
                to_proc = event.num_field("to_proc")

            self.processed_events.append(EventBinderCall(event.pid, event.ts / 1000,
                                event.cpu, event.name, reply, to_proc, flags, code))
        elif event.name == "mali_utilization_stats":
            return
            # self.event_count.mali_utilization_stats += 1
            #
            # util = event.num_field("util")
            # freq = event.num_field("norm_freq")
            # if freq is None:
            #     print "wait here"
            # self.processed_events.append(EventMaliUtil(event.pid, event.ts / 1000, event.cpu,
            #                     util, freq))
        elif event.name == "mali":
            self.event_count.mali += 1

            util = event.num_field("load")
            freq = event.num_field("freq") * 1000000

            self.processed_events.append(EventMaliUtil(event.pid, event.ts / 1000, event.cpu,
                                                       util, freq))
            # print "wait here"
        elif event.name == "exynos_temp":
            self.event_count.temp += 1

            big0 = event.num_field("t0")
            big1 = event.num_field("t1")
            big2 = event.num_field("t2")
            big3 = event.num_field("t3")
            little = (big0 + big1 + big2 + big3)/4
            GPU = event.num_field("t4")

