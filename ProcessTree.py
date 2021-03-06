#!/usr/bin/env python

__author__ = "Alex Hoffman"
__copyright__ = "Copyright 2019, Alex Hoffman"
__license__ = "GPL"
__version__ = "1.0"
__maintainer__ = "Alex Hoffman"
__email__ = "alex.hoffman@tum.de"
__status__ = "Beta"

import csv
import time
import os

import networkx as nx

from Dependencies import DependencyType
from HardwareBranches import *
from Optimizations import OptimizationInfoType
from ProcessBranch import ProcessBranch
from SystemEvents import *
from SystemMetrics import *


class ProcessTree:
    """ A tree of PID branches that represents all of the PIDs that are relevant to the target application
    """
    def __init__(self, pidtracer, metrics):

        self.metrics = metrics
        self.graph = nx.DiGraph()
        self.pidtracer = pidtracer

        self.process_branches = dict()
        self.binder_branches = dict()
        self.pending_binder_calls = []
        self.completed_binder_calls = []
        self.cpus = []

        self._create_cpu_branches()
        self.gpu = GPUBranch(self.metrics.current_gpu_freq,
                             self.metrics.current_gpu_util, self.graph)
        self._create_pid_branches()

        self.idle_time = 0
        self.temp_time = 0
        self.binder_time = 0
        self.mali_time = 0
        self.sched_switch_time = 0
        self.freq_time = 0

    def _create_cpu_branches(self):
        """ Creates a CPU branch for each CPU found in a system
        """
        for x in range(0, self.metrics.core_count):
            self.cpus.append(
                CPUBranch(
                    x,
                    self.metrics.current_core_freqs[x],
                    self.metrics.current_core_utils[x],
                    self.graph,
                ))

    def _create_pid_branches(self):
        """ Each PID in the tree creates a branch on which jobs and tasks of that PID are created in a
        chronological order such that the branch is a directed (in time) execution history of the
        branch's PID.
        """
        for i, pid in self.pidtracer.app_pids.iteritems():
            self.process_branches[i] = ProcessBranch(
                pid.pid,
                pid.pname,
                pid.tname,
                None,
                self.graph,
                self.pidtracer,
                self.cpus,
                self.gpu,
            )

        for i, pid in self.pidtracer.system_pids.iteritems():
            self.process_branches[i] = ProcessBranch(
                pid.pid,
                pid.pname,
                pid.tname,
                None,
                self.graph,
                self.pidtracer,
                self.cpus,
                self.gpu,
            )

        for i, pid in self.pidtracer.binder_pids.iteritems():
            self.binder_branches[i] = ProcessBranch(
                pid.pid,
                pid.pname,
                pid.tname,
                None,
                self.graph,
                self.pidtracer,
                self.cpus,
                self.gpu,
            )

    def finish_tree(self, filename, governor, subdir):
        """ After all events have been added to a tree the tree compiles its energy results and
        writes them to a CSV file. Summaries of each PID's energy consumption as well as total
        tree energy metrics are provided.

        :param filename: Filename prefix which is used to differentiate the current trace
        :param subdir: Sub directory to store results in (usefull if running multiple tests)
        """
        file_folder = "results/"

        if subdir:
            file_folder += subdir

        if not os.path.exists(file_folder):
            os.makedirs(file_folder)

        file_prefix = file_folder + filename

        with open(file_prefix + "_results.csv", "w+") as f:

            results_writer = csv.writer(f, delimiter=",")

            # Start and end time
            start_time = 0
            finish_time = 0
            for x, branch in self.process_branches.iteritems():

                if branch.tasks:

                    if branch.tasks[
                            0].start_time < start_time or start_time == 0:
                        start_time = branch.tasks[0].start_time

                    if (branch.tasks[-1].start_time + branch.tasks[-1].duration
                        ) > finish_time or finish_time == 0:
                        finish_time = (branch.tasks[-1].start_time +
                                       branch.tasks[-1].duration)

            results_writer.writerow(["Application", filename])
            results_writer.writerow(["Governor", governor])
            results_writer.writerow(["Start", start_time / 1000000.0])
            results_writer.writerow(["Finish", finish_time / 1000000.0])
            duration = (finish_time - start_time) * 0.000001
            results_writer.writerow(["Duration", duration])
            results_writer.writerow([])

            total_energy = 0.0
            # b2l_realloc, dvfs, same cluster realloc, dvfs after realloc
            optimizations_found = [0, 0, 0, 0]
            timeline_interval = 0.05
            timeline_intervals = int(round(duration / timeline_interval)) + 1
            optimization_timeline_total = np.full(timeline_intervals * 2,
                                                  [0]).reshape(
                                                      timeline_intervals, 2)

            with open(file_prefix + "_optimizations.csv", "w+") as f_op:
                op_writer = csv.writer(f_op, delimiter=",")
                op_writer.writerow([
                    "Op ID",
                    "Task ID",
                    "Task PID",
                    "Task Name",
                    "TS",
                    "Duration",
                    "Core",
                    "Freq",
                    "New Core",
                    "New Core's Old Freq",
                    "New Freq",
                    "Original Core's Prev Util",
                    "New Core's Prev Util",
                    "New Core's New Util",
                    "Optimization Type",
                ])
                op_writer.writerow([])

                error_branch = 0
                try:
                    for x in list(self.process_branches.keys()):
                        error_branch = x
                        branch = self.process_branches[x]

                        if len(branch.tasks) == 0:

                            del self.process_branches[x]
                            continue

                        branch_stats = branch.get_task_energy(
                            start_time, finish_time)
                        branch.energy = branch_stats.energy
                        for i in range(len(branch.energy)):
                            total_energy += branch.energy[i]
                        branch.duration = branch_stats.duration

                        if branch.energy == 0.0:
                            continue

                        results_writer.writerow([
                            branch.pid,
                            branch.pname,
                            branch.tname,
                            str(len(branch.tasks)),
                            branch.energy,
                            branch.duration,
                        ])

                        mf = self.metrics.energy_profile.migration_factor

                        ### OPTIMAL EVALUATION
                        error_task = 0
                        try:
                            for task in branch.tasks:
                                error_task = task.id

                                if (
                                        task.cpu_cycles == 0
                                ):  # Tasks that started at the end of the trace time
                                    continue

                                cores = self.metrics.sys_util_history
                                core_utils = [0.0] * 8
                                core_utils[0] = cores.cpu[0].get_util(
                                    task.finish_time)
                                core_utils[1] = cores.cpu[1].get_util(
                                    task.finish_time)
                                core_utils[2] = cores.cpu[2].get_util(
                                    task.finish_time)
                                core_utils[3] = cores.cpu[3].get_util(
                                    task.finish_time)
                                core_utils[4] = cores.cpu[4].get_util(
                                    task.finish_time)
                                core_utils[5] = cores.cpu[5].get_util(
                                    task.finish_time)
                                core_utils[6] = cores.cpu[6].get_util(
                                    task.finish_time)
                                core_utils[7] = cores.cpu[7].get_util(
                                    task.finish_time)

                                lf = self.metrics.energy_profile.little_freqs
                                bf = self.metrics.energy_profile.big_freqs

                                task_cycles = task.cpu_cycles

                                # Reallocate to small core
                                if (
                                        task.events[0].cpu > 3
                                ):  # big TODO fix the use of the first event's CPU

                                    little_core_index = np.argmin(
                                        core_utils[:4]
                                    )  # Core with most capacity
                                    little_cores = core_utils[:4]

                                    cur_core_util = core_utils[
                                        task.events[0].cpu]
                                    # target_core_util = core_utils[little_core_index]

                                    cur_little_cpu_freq = float(
                                        task.events[0].cpu_freq[0])

                                    cycles_on_little = round(task_cycles * mf)

                                    for little_freq in lf:

                                        # Scaled little utils
                                        if cur_little_cpu_freq != little_freq:
                                            scaling_factor = (
                                                cur_little_cpu_freq /
                                                little_freq)
                                            core_utils_new_freq = [
                                                core * scaling_factor
                                                for core in little_cores
                                            ]
                                        else:
                                            core_utils_new_freq = little_cores

                                        # Check existing workload can be fit onto CPU at new frequency
                                        if all(core_util <= 100.0 for core_util
                                               in core_utils_new_freq):

                                            # Realloc to little
                                            available_cycles_on_little_at_new_freq = round(
                                                (1.0 - (core_utils_new_freq[
                                                    little_core_index] / 100))
                                                * little_freq)

                                            required_duration = (
                                                cycles_on_little /
                                                available_cycles_on_little_at_new_freq
                                                * 1000000)

                                            finish_time_on_little = int(
                                                round(task.start_time +
                                                      required_duration))

                                            new_util_on_target_core = core_utils_new_freq[
                                                little_core_index] + (
                                                    cycles_on_little /
                                                    little_freq * 100)

                                            try:
                                                depender_start_time = (
                                                    task.dependency.next_task.
                                                    start_time)
                                            except Exception as e:
                                                continue

                                            if (finish_time_on_little <
                                                    depender_start_time):
                                                task.optimization_info.add_optim_type(
                                                    OptimizationInfoType.
                                                    B2L_REALLOC)
                                                optimizations_found[0] += 1

                                                if (little_freq != task.
                                                        events[0].cpu_freq[0]):
                                                    task.optimization_info.add_optim_type(
                                                        OptimizationInfoType.
                                                        DVFS_AFTER_REALLOC)
                                                    optimizations_found[3] += 1

                                                task.optimization_info.set_message(
                                                    "Task can be reallocated")

                                                op_writer.writerow([
                                                    task.optimization_info.ID,
                                                    task.id,
                                                    task.pid,
                                                    task.name,
                                                    task.start_time,
                                                    task.duration,
                                                    task.events[0].cpu,
                                                    task.events[0].cpu_freq[
                                                        0 if task.events[0].
                                                        cpu < 4 else 1],
                                                    little_core_index,
                                                    task.events[0].cpu_freq[0],
                                                    little_freq,
                                                    cur_core_util,
                                                    cur_core_util,
                                                    new_util_on_target_core,
                                                    str(task.optimization_info
                                                        ),
                                                ])

                                                break

                                # Current core not running at minimum DVFS
                                if (task.events[0].cpu <= 3
                                        and task.events[0].cpu_freq[0] != lf[0]
                                    ) or (task.events[0].cpu >= 4 and
                                          task.events[0].cpu_freq[1] != bf[0]):

                                    cur_cpu_freq = float(
                                        task.events[0].
                                        cpu_freq[0 if task.events[0].
                                                 cpu <= 3 else 1])

                                    if task.events[0].cpu <= 3:  # LITTLE
                                        freq_index = lf.index(cur_cpu_freq)
                                        freqs = lf[:
                                                   freq_index]  # Freqs from minimum freq until the current one
                                        lowest_util_core_index = np.argmin(
                                            core_utils[:4])

                                    else:  # big
                                        freq_index = bf.index(cur_cpu_freq)
                                        freqs = bf[:freq_index]
                                        lowest_util_core_index = (
                                            np.argmin(core_utils[4:]) + 4)

                                    # Utilization of core that task is currently running on
                                    cur_core_util = core_utils[
                                        task.events[0].cpu]

                                    target_core_util = core_utils[
                                        task.events[0].cpu]

                                    if (lowest_util_core_index !=
                                            task.events[0].cpu
                                        ):  # Might be a better core in cluster

                                        # Utilization of core in cluster with smallest load
                                        target_core_util = core_utils[
                                            lowest_util_core_index]

                                        # Load generated from the target task
                                        task_load = (
                                            float(task.duration) /
                                            self.metrics.sys_util_history.
                                            cpu[0].uw.window_duration * 100)

                                        # Current core utilization less the task of interest's load
                                        cur_core_util_wo_task = (
                                            cur_core_util - task_load)

                                        # If reallocation would result in a lower max utilization between current and target
                                        # core
                                        if cur_core_util_wo_task > target_core_util:
                                            core_utils[task.events[0].
                                                       cpu] -= task_load
                                            core_utils[
                                                lowest_util_core_index] += task_load
                                            task.optimization_info.add_optim_type(
                                                OptimizationInfoType.
                                                SAME_CLUSTER_REALLOC)
                                            optimizations_found[2] += 1

                                    for freq in freqs:

                                        # Scale
                                        scaling_factor = cur_cpu_freq / freq
                                        if task.events[0].cpu <= 3:  # LITTLE
                                            core_utils_new_freq = [
                                                core * scaling_factor
                                                for core in core_utils[:4]
                                            ]
                                        else:  # big
                                            core_utils_new_freq = [
                                                core * scaling_factor
                                                for core in core_utils[4:]
                                            ]

                                        if all(core_util <= 100.0 for core_util
                                               in core_utils_new_freq):

                                            task.optimization_info.set_message(
                                                "DVFS optimization possible")
                                            task.optimization_info.add_optim_type(
                                                OptimizationInfoType.DVFS)
                                            optimizations_found[1] += 1

                                            op_writer.writerow([
                                                task.optimization_info.ID,
                                                task.id,
                                                task.pid,
                                                task.name,
                                                task.start_time,
                                                task.duration,
                                                task.events[0].cpu,
                                                task.events[0].
                                                cpu_freq[0 if task.events[0].
                                                         cpu < 4 else 1],
                                                lowest_util_core_index,
                                                cur_cpu_freq,
                                                freq,
                                                cur_core_util,
                                                target_core_util,
                                                core_utils_new_freq[
                                                    lowest_util_core_index %
                                                    4],
                                                str(task.optimization_info),
                                            ])
                                            break
                        except Exception, e:
                            e = str(e) + " task {}".format(error_task)
                            raise Exception(e)

                        optimization_timeline = branch.get_optimization_timeline(
                            start_time, timeline_intervals,
                            timeline_interval * 1000000)
                        optimization_timeline_total = np.add(
                            optimization_timeline_total, optimization_timeline)
                except Exception, e:
                    e = str(e) + " in branch {}".format(error_branch)
                    raise Exception(e)

            results_writer.writerow([])
            results_writer.writerow([
                "Optimizations", "B2L Reallocations", "DVFS",
                "Realloc in cluster", "DVFS after "
                "realloc"
            ])
            results_writer.writerow([
                "", optimizations_found[0], optimizations_found[1],
                optimizations_found[2], optimizations_found[3]
            ])
            results_writer.writerow([])

            results_writer.writerow(["Optimization Timeline"])
            results_writer.writerow([
                "TS (uS)", "Offset (S)", "DVFS Count", "Realloc Count",
                "Total Count"
            ])

            total_timeline_dvfs = 0
            total_timeline_realloc = 0
            for i in range(optimization_timeline_total.shape[0]):
                total_timeline_realloc += optimization_timeline_total[i][0]
                total_timeline_dvfs += optimization_timeline_total[i][1]
                offset = timeline_interval * i
                results_writer.writerow([
                    start_time + offset * 1000000,
                    offset,
                    optimization_timeline_total[i][1],
                    optimization_timeline_total[i][0],
                    optimization_timeline_total[i][0] +
                    optimization_timeline_total[i][1],
                ])

            results_writer.writerow([
                "",
                "Totals",
                total_timeline_dvfs,
                total_timeline_realloc,
                total_timeline_dvfs + total_timeline_realloc,
            ])
            results_writer.writerow([])

            results_writer.writerow([
                "PID",
                "Process Name",
                "Thread Name",
                "Task Count",
                "Energy",
                "Duration",
            ])

            # Calculate GPU energy
            gpu_energy = self.metrics.sys_util_history.gpu.get_energy(
                start_time, finish_time)
            results_writer.writerow(["GPU", gpu_energy])

            total_energy += gpu_energy

            results_writer.writerow([])
            results_writer.writerow(["Total Energy", total_energy])
            try:
                results_writer.writerow(
                    ["Average wattage", total_energy / duration])
            except ZeroDivisionError:
                print "No events were recorded!"

            results_writer.writerow([])
            results_writer.writerow(["Energy Timeline"])

            energy_timeline = [[(0.0, 0.0), 0.0, (0.0, 0.0, 0.0), 0.0, 0]
                               for _ in range(timeline_intervals)]

            for i, second in enumerate(energy_timeline):

                for x, branch in self.process_branches.iteritems():
                    energy = branch.get_interval_energy(
                        i, timeline_interval, start_time, finish_time)
                    new_energy = [
                        second[0][0] + energy[0], second[0][1] + energy[1]
                    ]
                    second[0] = new_energy

            for i, second in enumerate(energy_timeline):

                second[
                    1] += self.metrics.sys_util_history.gpu.get_interval_energy(
                        i, timeline_interval, start_time, finish_time)
                temp_l = SystemMetrics.current_metrics.get_temp(
                    i * timeline_interval * 1000000, 0)
                temp_b = SystemMetrics.current_metrics.get_temp(
                    i * timeline_interval * 1000000, 4)
                temp_g = SystemMetrics.current_metrics.get_temp(
                    i * timeline_interval * 1000000, -1)
                second[2] = (temp_b, temp_l, temp_g)
                second[
                    3] = SystemMetrics.current_metrics.sys_util_history.gpu.get_util(
                        i * timeline_interval * 1000000)
                second[
                    4] = SystemMetrics.current_metrics.sys_util_history.gpu.get_freq(
                        i * timeline_interval * 1000000)

            results_writer.writerow([
                "Absolute Time",
                "Sec Offset",
                "Thread Energy",
                "Big Energy",
                "Little Energy",
                "GPU Energy",
                "Total Energy",
                "Temps",
                "GPU Util",
                "GPU Freq",
            ])

            for x, second in enumerate(energy_timeline):
                results_writer.writerow([
                    str(x * timeline_interval + start_time / 1000000.0),
                    str(x * timeline_interval),
                    str(second[0][0] + second[0][1]),
                    str(second[0][1]),
                    str(second[0][0]),
                    str(second[1]),
                    str(second[0][0] + second[0][1] + second[1]),
                    str(second[2]),
                    str(second[3]),
                    str(second[4]),
                ])

            return optimizations_found

    def handle_event(self, event, subgraph):
        """
        An event is handled by and added to the current trace tree, handled depending on event type.

        :param event: The event to be added into the tree
        :param subgraph: Boolean to enable to drawing of the task graph's node's sub-graphs
        :return 0 on success
        """
        proc_start_time = time.time()

        # Set event freq
        event.cpu_freq[0] = self.metrics.get_cpu_core_freq(0)
        event.cpu_freq[1] = self.metrics.get_cpu_core_freq(4)
        event.gpu_freq = self.metrics.current_gpu_freq
        event.gpu_util = self.metrics.current_gpu_util

        if isinstance(event, EventSchedSwitch):  # PID context swap

            # Task being switched out, ignoring idle task and binder threads
            if event.pid != 0 and (event.next_pid in self.pidtracer.system_pids
                                   or
                                   event.next_pid in self.pidtracer.app_pids):

                try:
                    process_branch = self.process_branches[event.pid]
                    process_branch.add_event(
                        event,
                        event_type=JobType.SCHED_SWITCH_OUT,
                        subgraph=subgraph)

                except KeyError:
                    pass  # PID not of interest to program

            # Task being switched in, again ignoring idle task and binder threads
            if event.next_pid != 0 and (
                    event.next_pid in self.pidtracer.system_pids
                    or event.next_pid in self.pidtracer.app_pids):

                for x, pending_binder_node in reversed(
                        list(enumerate(
                            self.completed_binder_calls))):  # Most recent

                    # If event to be switched in is the target of the Binder transaction
                    if event.next_pid == pending_binder_node.target_pid:

                        # If async binder call (no binder thread)
                        if pending_binder_node.transaction_type == BinderType.ASYNC:
                            # Calling PID acts as binder thread and should be added to binder threads if not already
                            # added
                            if (pending_binder_node.caller_pid not in
                                    self.binder_branches):
                                pid_info = self.pidtracer.get_pid_info(
                                    pending_binder_node.caller_pid)

                                if not pid_info:
                                    del self.completed_binder_calls[x]
                                    break

                                self.binder_branches[
                                    pending_binder_node.
                                    caller_pid] = ProcessBranch(
                                        pid_info.pid,
                                        pid_info.pname,
                                        pid_info.tname,
                                        None,
                                        self.graph,
                                        self.pidtracer,
                                        self.cpus,
                                        self.gpu,
                                    )

                                self.pidtracer.binder_pids[
                                    pending_binder_node.
                                    binder_thread] = pid_info

                        else:  # Sync
                            # Binder thread that is not yet known
                            if (pending_binder_node.binder_thread not in
                                    self.binder_branches):
                                pid_info = self.pidtracer.find_pid_info(
                                    pending_binder_node.binder_thread)

                                if not pid_info:
                                    del self.completed_binder_calls[x]
                                    break

                                self.binder_branches[
                                    pending_binder_node.
                                    binder_thread] = ProcessBranch(
                                        pid_info.pid,
                                        pid_info.pname,
                                        pid_info.tname,
                                        None,
                                        self.graph,
                                        self.pidtracer,
                                        self.cpus,
                                        self.gpu,
                                    )

                                self.pidtracer.binder_pids[
                                    pending_binder_node.
                                    binder_thread] = pid_info

                        # If target thread is not yet known
                        if event.next_pid not in self.process_branches:
                            # Calling to a PID that was not initially found as belonging to app
                            pid_info = self.pidtracer.find_pid_info(
                                event.next_pid)

                            if not pid_info:
                                del self.completed_binder_calls[x]
                                break

                            self.process_branches[
                                event.next_pid] = ProcessBranch(
                                    pid_info.pid,
                                    pid_info.pname,
                                    pid_info.tname,
                                    None,
                                    self.graph,
                                    self.pidtracer,
                                    self.cpus,
                                    self.gpu,
                                )

                            self.pidtracer.app_pids[event.next_pid] = pid_info

                        # Add first half binder event to binder branch
                        if pending_binder_node.first_half:
                            self.binder_branches[
                                pending_binder_node.binder_thread].add_event(
                                    pending_binder_node.first_half,
                                    event_type=JobType.BINDER_SEND,
                                )
                        else:  # Async binder transaction
                            self.binder_branches[
                                pending_binder_node.binder_thread].add_event(
                                    pending_binder_node.second_half,
                                    event_type=JobType.BINDER_SEND,
                                )

                        # Add second half binder event to binder branch
                        self.binder_branches[
                            pending_binder_node.binder_thread].add_event(
                                pending_binder_node.second_half,
                                event_type=JobType.BINDER_RECV,
                            )

                        try:
                            self.graph.add_edge(  # Edge from calling task to binder node
                                self.process_branches[
                                    pending_binder_node.caller_pid].tasks[-1],
                                self.binder_branches[
                                    pending_binder_node.binder_thread].
                                binder_tasks[-1],
                                color="palevioletred3",
                                dir="forward",
                                style="bold",
                            )

                            # Switch in new pid which will find pending completed binder transaction and create a
                            # new task node
                            self.process_branches[
                                pending_binder_node.target_pid].add_event(
                                    event,
                                    event_type=JobType.SCHED_SWITCH_IN,
                                    subgraph=subgraph)

                            self.graph.add_edge(  # Edge from binder node to next task
                                self.binder_branches[
                                    pending_binder_node.binder_thread].
                                binder_tasks[-1],
                                self.process_branches[
                                    pending_binder_node.target_pid].tasks[-1],
                                color="yellow3",
                                dir="forward",
                            )

                            # Create dependency
                            self.process_branches[
                                pending_binder_node.target_pid].tasks[
                                    -1].dependency.type = DependencyType.BINDER

                        except IndexError:
                            pass  # Calling task has no nodes yet to link, tracing started during transaction

                        if (pending_binder_node.target_pid ==
                                pending_binder_node.caller_pid
                            ):  # Task signaling itself
                            # Create dependency from current task to calling task
                            try:
                                self.process_branches[
                                    pending_binder_node.target_pid].tasks[
                                        -1].dependency.prev_task = self.process_branches[
                                            pending_binder_node.
                                            caller_pid].tasks[-2]

                                # Create dependency from calling task to current task
                                self.process_branches[
                                    pending_binder_node.caller_pid].tasks[
                                        -2].dependency.next_task = self.process_branches[
                                            pending_binder_node.
                                            target_pid].tasks[-1]
                            except IndexError:  # First task for PID
                                pass

                        else:
                            if self.process_branches[
                                    pending_binder_node.caller_pid].tasks[-1]:
                                # Create dependency from current task to calling task
                                self.process_branches[
                                    pending_binder_node.target_pid].tasks[
                                        -1].dependency.prev_task = self.process_branches[
                                            pending_binder_node.
                                            caller_pid].tasks[-1]

                                # Create dependency from calling task to current task
                                self.process_branches[
                                    pending_binder_node.caller_pid].tasks[
                                        -1].dependency.next_task = self.process_branches[
                                            pending_binder_node.
                                            target_pid].tasks[-1]

                        # remove binder task that is now complete
                        del self.completed_binder_calls[x]

                        self.sched_switch_time += time.time() - proc_start_time
                        return 0

                # Not called from a Binder transaction (cyclic task)
                try:
                    self.process_branches[event.next_pid].add_event(
                        event,
                        event_type=JobType.SCHED_SWITCH_IN,
                        subgraph=subgraph)
                except KeyError:
                    pass  # Branch (PID) is not of interest and as such can be passed

            self.sched_switch_time += time.time() - proc_start_time
            return 0

        elif isinstance(event, EventBinderTransaction):

            # Normal calls and async calls (first halves)
            if event.trans_type == BinderType.CALL:

                # First half of a binder transaction
                if (event.pid in self.pidtracer.app_pids
                        or event.pid in self.pidtracer.system_pids):

                    self.pending_binder_calls.append(
                        FirstHalfBinderTransaction(event, event.target_pid,
                                                   self.pidtracer))

            elif event.trans_type == BinderType.ASYNC:

                if (event.pid in self.pidtracer.app_pids
                        or event.pid in self.pidtracer.system_pids):

                    self.completed_binder_calls.append(
                        CompletedBinderTransaction(event))

            elif event.trans_type == BinderType.REPLY:

                if (event.pid in self.pidtracer.system_pids
                        or event.pid in self.pidtracer.binder_pids):

                    if self.pending_binder_calls:  # Pending first halves
                        # Find most recent first half
                        for x, transaction in reversed(
                                list(enumerate(self.pending_binder_calls))):

                            if (any(pid == event.pid
                                    for pid in transaction.child_pids)
                                    or event.pid == transaction.parent_pid
                                ):  # Find corresponding first half

                                self.completed_binder_calls.append(
                                    CompletedBinderTransaction(
                                        event, transaction.send_event))

                                del self.pending_binder_calls[
                                    x]  # Remove completed first half

            self.binder_time += time.time() - proc_start_time
            return 0

        elif isinstance(event, EventFreqChange):

            for i in range(event.target_cpu, event.target_cpu + 4):
                self.metrics.current_core_freqs[i] = event.freq
                self.metrics.current_core_utils[i] = event.util
                self.cpus[i].add_event(event)

            self.freq_time += time.time() - proc_start_time
            return 0

        elif isinstance(event, EventMaliUtil):

            self.metrics.current_gpu_freq = event.freq
            self.metrics.current_gpu_util = event.util
            self.metrics.sys_util_history.gpu.add_event(event)
            self.gpu.add_event(event)

            self.mali_time += time.time() - proc_start_time
            return 0

    @staticmethod
    def handle_temp_event(event, event_n_minus_1):

        value = TempLogEntry(
            event.time,
            event.big0,
            event.big1,
            event.big2,
            event.big3,
            event.little,
            event.gpu,
        )

        if not event_n_minus_1:
            return np.full(1, value)
        else:
            duration = event.time - event_n_minus_1.time
            return np.full(duration, [value])

    def handle_idle_event(self, event):

        self.metrics.sys_util_history.cpu[event.cpu].add_idle_event(event)
