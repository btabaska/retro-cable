import Foundation
import Observation

/// What is on the tuned channel and the player playing it (web/src/hooks/useTuner.ts plus
/// components/Player.tsx): the /tune queue, the start position, advancing at slot end, refilling
/// the queue, media failures and retries. It drives one `PlaybackEngine`, kept warm between tunes.
///
/// Every tune starts on the press: the old picture and sound stop at once and only the latest
/// request's answer is used, so rapid flipping lands on the last channel pressed.
@MainActor
@Observable
public final class Tuner {
    /// Retry delay for a failed /tune.
    public static let retrySeconds: Double = 5
    /// A file still playing with at most this much left at slot end finishes first.
    public static let overrunTolerance: Double = 5
    /// Never start closer than this to the end of a file.
    public static let endClamp: Double = 0.25
    /// A file differing from its slot by more than this is logged.
    public static let mismatchSeconds: Double = 3
    /// The slot-end timer fires this long after the end, so simulated now is past it.
    static let endSlack: Double = 0.05
    /// After the last few seconds of an overrunning file, advance even without an end event.
    static let tailSlack: Double = 0.25

    public private(set) var channel: ChannelInfo?
    public private(set) var year: Int = Years.defaultYear
    /// The answer's slots: current first, then the ones after it.
    public private(set) var queue: [TunedSlot] = []
    public private(set) var index = 0
    public private(set) var picture: Picture = .black
    public private(set) var demoSlate: DemoSlate?
    /// The server's simulated now from the latest answer, carried on the monotonic clock.
    public private(set) var simTime: SimulatedTime?
    public private(set) var latency = TuneLatencyLog()
    /// The zone for "Up next 8:27" on the bumper slate: the lineup's.
    public var zone = "America/New_York"
    /// Called whenever the slot playing changes (watch reporting listens).
    @ObservationIgnored public var onSlotChange: (@MainActor () -> Void)?

    public var slot: TunedSlot? { queue.indices.contains(index) ? queue[index] : nil }
    public var upcoming: [TunedSlot] { index + 1 < queue.count ? Array(queue[(index + 1)...]) : [] }
    /// The next program, skipping bumpers: the banner's "Next" and the bumper slate's "Up next".
    public var nextProgram: TunedSlot? { upcoming.first { $0.kind != .bumper } }

    private enum Reason {
        /// A new channel or year: start the answer's current slot.
        case tune
        /// The queue is running low: keep the slot playing if the answer still has it.
        case refill
        /// The file failed: start the answer's current slot from a fresh URL.
        case mediaRetry
    }

    @ObservationIgnored private let fetch: @Sendable (String, Int) async throws -> Tune
    @ObservationIgnored private let engine: any PlaybackEngine
    @ObservationIgnored private let clock: any MonotonicClock
    @ObservationIgnored private let timers: any TimerScheduler
    @ObservationIgnored private let mediaURL: (SignedPath) -> URL?
    @ObservationIgnored private let log: @MainActor (String) -> Void

    /// The latest request; answers to any earlier one are dropped.
    @ObservationIgnored private var serial = 0
    @ObservationIgnored private var requesting = false
    @ObservationIgnored private var fetches: [Int: Task<Void, Never>] = [:]
    @ObservationIgnored private var retryTimer: TimerHandle?
    @ObservationIgnored private var endTimer: TimerHandle?
    @ObservationIgnored private var tailTimer: TimerHandle?
    /// The current load; events for any other are stale.
    @ObservationIgnored private var playbackID = 0
    @ObservationIgnored private var awaitingTail = false
    /// The start of a slot whose file failed and was retuned once.
    @ObservationIgnored private var retriedSlot: Date?
    /// The start of a slot whose file failed twice: filler until it ends.
    @ObservationIgnored private var failedSlot: Date?
    /// When the press that started this tune happened (monotonic), until its first frame.
    @ObservationIgnored private var pressedAt: TimeInterval?

    public init(
        fetch: @escaping @Sendable (String, Int) async throws -> Tune,
        engine: any PlaybackEngine,
        clock: any MonotonicClock,
        timers: any TimerScheduler,
        mediaURL: @escaping (SignedPath) -> URL?,
        log: @escaping @MainActor (String) -> Void
    ) {
        self.fetch = fetch
        self.engine = engine
        self.clock = clock
        self.timers = timers
        self.mediaURL = mediaURL
        self.log = log
        engine.onEvent = { [weak self] event in self?.handle(event) }
    }

    /// Simulated now, or nil before the first answer.
    public func simNow() -> Date? {
        simTime?.now(at: clock.seconds())
    }

    // MARK: - Commands

    /// Tune `channel` in `year` now: stop the old picture and sound, show black, ask /tune.
    /// `pressedAt` (monotonic seconds) is when the viewer pressed; it defaults to now.
    public func tune(_ channel: ChannelInfo, year: Int, pressedAt: TimeInterval? = nil) {
        reset()
        self.channel = channel
        self.year = year
        self.pressedAt = pressedAt ?? clock.seconds()
        onSlotChange?()
        request(.tune)
    }

    /// Stop playing and forget the queue, keeping the channel: going to the background, or the
    /// year picker hiding the picture. Nothing stays scheduled.
    public func stop() {
        reset()
        pressedAt = nil
        onSlotChange?()
    }

    private func reset() {
        serial += 1
        requesting = false
        for task in fetches.values { task.cancel() }
        cancelTimers()
        retryTimer?.cancel()
        retryTimer = nil
        engine.stop()
        playbackID += 1
        queue = []
        index = 0
        picture = .black
        demoSlate = nil
        retriedSlot = nil
        failedSlot = nil
    }

    /// Waits for every request in flight (tests).
    func settled() async {
        while let task = fetches.values.first {
            await task.value
        }
    }

    // MARK: - Requests

    private func request(_ reason: Reason) {
        guard let channel else { return }
        serial += 1
        let mine = serial
        requesting = true
        retryTimer?.cancel()
        retryTimer = nil
        let fetch = self.fetch
        let id = channel.id
        let year = self.year
        fetches[mine] = Task { [weak self] in
            let result: Result<Tune, Error>
            do {
                result = .success(try await fetch(id, year))
            } catch {
                result = .failure(error)
            }
            guard let self else { return }
            self.fetches[mine] = nil
            guard mine == self.serial else { return }
            self.requesting = false
            switch result {
            case .success(let tune): self.answer(tune, reason)
            case .failure: self.failed(reason)
            }
        }
    }

    private func answer(_ tune: Tune, _ reason: Reason) {
        simTime = SimulatedTime(serverNow: tune.now, receivedAt: clock.seconds())
        let fresh = [tune.current] + tune.next
        if reason == .refill, let playing = slot,
           let k = fresh.firstIndex(where: { $0.start == playing.start && $0.kind == playing.kind })
        {
            // Same slot still playing: take the new queue around it, don't restart it.
            queue = fresh
            index = k
            scheduleEnd()
            return
        }
        queue = fresh
        index = 0
        startSlot()
    }

    private func failed(_ reason: Reason) {
        switch reason {
        case .tune, .refill:
            retryTimer = timers.schedule(after: Self.retrySeconds) { [weak self] in
                self?.request(reason)
            }
        case .mediaRetry:
            // No fresh URL to be had: the filler card until the slot ends.
            if let slot {
                failedSlot = slot.start
                picture = .filler(title: slot.title)
            }
        }
    }

    // MARK: - Slots

    private func startSlot() {
        cancelTimers()
        guard let slot, let channel else { return }
        scheduleEnd()
        playbackID += 1
        demoSlate = DemoSlate.make(slot, channel: channel, next: nextProgram?.slot)
        if failedSlot == slot.start {
            engine.stop()
            picture = .filler(title: slot.title)
        } else if let path = slot.mediaPath, let url = mediaURL(path) {
            picture = .black
            engine.load(url, id: playbackID)
        } else {
            engine.stop()
            picture = slot.kind == .bumper
                ? .bumperSlate(BumperSlate.make(channel: channel, next: nextProgram?.slot, zone: zone))
                : .filler(title: slot.title)
            firstFrame()
        }
        onSlotChange?()
    }

    private func scheduleEnd() {
        endTimer?.cancel()
        guard let slot, let now = simNow() else { return }
        let wait = max(0, slot.end.timeIntervalSince(now)) + Self.endSlack
        endTimer = timers.schedule(after: wait) { [weak self] in self?.slotEnded() }
    }

    private func cancelTimers() {
        endTimer?.cancel()
        endTimer = nil
        tailTimer?.cancel()
        tailTimer = nil
        awaitingTail = false
    }

    /// The schedule says the slot is over. Programs are never cut: a file a few seconds behind
    /// finishes first.
    private func slotEnded() {
        endTimer = nil
        if picture == .video, let left = engine.secondsLeft(), left > 0, left <= Self.overrunTolerance {
            awaitingTail = true
            tailTimer = timers.schedule(after: left + Self.tailSlack) { [weak self] in self?.advance() }
            return
        }
        advance()
    }

    private func advance() {
        cancelTimers()
        guard index + 1 < queue.count else {
            // Ran out of slots before a refill answered: black ("Tuning…") until it does.
            engine.stop()
            playbackID += 1
            picture = .black
            demoSlate = nil
            queue = []
            index = 0
            onSlotChange?()
            if !requesting, retryTimer == nil { request(.refill) }
            return
        }
        index += 1
        retriedSlot = nil
        // Refill before it runs out: once the slot playing is the second to last fetched.
        if index >= queue.count - 2, !requesting { request(.refill) }
        startSlot()
    }

    // MARK: - Player events

    public func handle(_ event: PlaybackEvent) {
        switch event {
        case .ready(let id, let duration):
            guard id == playbackID, let slot, let now = simNow() else { return }
            if let duration { checkLength(slot, duration) }
            var offset = max(0, now.timeIntervalSince(slot.start))
            if let duration { offset = max(0, min(offset, duration - Self.endClamp)) }
            engine.start(id: id, at: offset)
        case .firstFrame(let id):
            guard id == playbackID else { return }
            picture = .video
            firstFrame()
        case .failed(let id):
            guard id == playbackID, let slot else { return }
            engine.stop()
            playbackID += 1
            if retriedSlot != slot.start {
                retriedSlot = slot.start
                picture = .black
                request(.mediaRetry)
            } else {
                failedSlot = slot.start
                picture = .filler(title: slot.title)
            }
        case .ended(let id):
            guard id == playbackID else { return }
            if awaitingTail {
                advance()
            } else {
                // Shorter than its slot: black until the schedule moves on.
                picture = .black
            }
        }
    }

    private func firstFrame() {
        guard let pressedAt, let channel else { return }
        self.pressedAt = nil
        let entry = TuneLatency(
            channel: channel.id, milliseconds: Int(((clock.seconds() - pressedAt) * 1000).rounded())
        )
        latency.record(entry)
        log(entry.logLine)
    }

    private func checkLength(_ slot: TunedSlot, _ duration: Double) {
        if let line = LiveText.lengthMismatch(slot.slot, fileSeconds: duration, tolerance: Self.mismatchSeconds) {
            log(line)
        }
    }
}
