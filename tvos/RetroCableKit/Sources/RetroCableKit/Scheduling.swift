import Foundation

/// A pending timer; `cancel` stops it from firing. Cancelling twice, or after it fired, is fine.
@MainActor
public final class TimerHandle {
    private var onCancel: (() -> Void)?

    public init(onCancel: @escaping () -> Void) {
        self.onCancel = onCancel
    }

    public func cancel() {
        onCancel?()
        onCancel = nil
    }
}

/// Runs main-actor work after a delay on the real clock. Tasks in the app; a manual clock in tests,
/// so no test ever waits for real time.
public protocol TimerScheduler: Sendable {
    @MainActor
    func schedule(after seconds: Double, _ action: @escaping @MainActor () -> Void) -> TimerHandle
}

public struct TaskTimerScheduler: TimerScheduler {
    public init() {}

    @MainActor
    public func schedule(after seconds: Double, _ action: @escaping @MainActor () -> Void) -> TimerHandle {
        let task = Task { @MainActor in
            try? await Task.sleep(for: .seconds(max(0, seconds)))
            guard !Task.isCancelled else { return }
            action()
        }
        return TimerHandle { task.cancel() }
    }
}
