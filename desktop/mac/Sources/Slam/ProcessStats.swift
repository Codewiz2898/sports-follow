import Darwin
import Foundation

/// Our own cost, read from the kernel: CPU time (as % of one core over the interval), memory
/// footprint (what Activity Monitor shows as Memory), and the kernel's energy estimate.
final class ProcessStats {
    private var lastWall = DispatchTime.now().uptimeNanoseconds
    private var lastCPU = ProcessStats.cpuSeconds()
    private var lastEnergy = ProcessStats.usageInfo()?.ri_energy_nj ?? 0

    static func cpuSeconds() -> Double {
        var ru = Darwin.rusage()
        getrusage(RUSAGE_SELF, &ru)
        func s(_ t: timeval) -> Double { Double(t.tv_sec) + Double(t.tv_usec) / 1e6 }
        return s(ru.ru_utime) + s(ru.ru_stime)
    }

    static func usageInfo() -> rusage_info_v6? {
        var info = rusage_info_v6()
        let st = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: rusage_info_t?.self, capacity: 1) {
                proc_pid_rusage(getpid(), RUSAGE_INFO_V6, $0)
            }
        }
        return st == 0 ? info : nil
    }

    static func footprintMB() -> Double {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
        let kr = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        return kr == KERN_SUCCESS ? Double(info.phys_footprint) / 1_048_576 : -1
    }

    /// (cpu % of one core, average power in mW) since the last call.
    func sample() -> (cpu: Double, milliwatts: Double) {
        let now = DispatchTime.now().uptimeNanoseconds
        let cpu = ProcessStats.cpuSeconds()
        let energy = ProcessStats.usageInfo()?.ri_energy_nj ?? lastEnergy
        let wall = Double(now - lastWall) / 1e9
        defer { lastWall = now; lastCPU = cpu; lastEnergy = energy }
        guard wall > 0 else { return (0, 0) }
        return ((cpu - lastCPU) / wall * 100, Double(energy &- lastEnergy) / 1e6 / wall)
    }
}
