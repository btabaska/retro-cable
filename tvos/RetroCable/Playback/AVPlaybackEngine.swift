import AVFoundation
import RetroCableKit

/// The app's one AVPlayer, kept for the whole session so a tune only swaps the item (TV-40).
/// It reports what happens to each load by id; the Tuner (RetroCableKit) decides everything.
/// Never log a URL, an item's error (its text can carry the URL), `exp`, `sig` or a token.
@MainActor
final class AVPlaybackEngine: PlaybackEngine {
    let player = AVPlayer()
    var onEvent: (@MainActor (PlaybackEvent) -> Void)?

    /// Seek tolerance: landing on a keyframe up to 1 s either side starts sooner than an exact
    /// seek, and keeps the picture well within TV-7's 2 s of the schedule.
    private static let seekTolerance = CMTime(seconds: 1, preferredTimescale: 600)

    private var loadID = 0
    private var started = false
    private var reportedFirstFrame = false
    private var statusObservation: NSKeyValueObservation?
    private var timeControlObservation: NSKeyValueObservation?
    private var itemObservers: [NSObjectProtocol] = []

    init() {
        player.automaticallyWaitsToMinimizeStalling = true
        player.allowsExternalPlayback = false
        player.preventsDisplaySleepDuringVideoPlayback = true
        timeControlObservation = player.observe(\.timeControlStatus, options: [.new]) { [weak self] player, _ in
            let playing = player.timeControlStatus == .playing
            Task { @MainActor [weak self] in self?.timeControlChanged(playing: playing) }
        }
    }

    func load(_ url: URL, id: Int) {
        clearItem()
        loadID = id
        let item = AVPlayerItem(asset: AVURLAsset(url: url))
        statusObservation = item.observe(\.status, options: [.new]) { [weak self] item, _ in
            let status = item.status
            let duration = item.duration.seconds
            Task { @MainActor [weak self] in self?.statusChanged(status, duration: duration, id: id) }
        }
        let center = NotificationCenter.default
        itemObservers = [
            center.addObserver(forName: AVPlayerItem.didPlayToEndTimeNotification, object: item, queue: .main) { [weak self] _ in
                Task { @MainActor [weak self] in self?.send(.ended(id: id), for: id) }
            },
            center.addObserver(forName: AVPlayerItem.failedToPlayToEndTimeNotification, object: item, queue: .main) { [weak self] _ in
                Task { @MainActor [weak self] in self?.send(.failed(id: id), for: id) }
            },
        ]
        player.replaceCurrentItem(with: item)
    }

    func start(id: Int, at seconds: Double) {
        guard id == loadID, player.currentItem != nil else { return }
        let time = CMTime(seconds: max(0, seconds), preferredTimescale: 600)
        player.seek(to: time, toleranceBefore: Self.seekTolerance, toleranceAfter: Self.seekTolerance) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, id == self.loadID else { return }
                self.started = true
                // Start as soon as there is a frame; AVPlayer still handles any later stall.
                self.player.playImmediately(atRate: 1)
                self.timeControlChanged(playing: self.player.timeControlStatus == .playing)
            }
        }
    }

    func stop() {
        clearItem()
        loadID = -1
    }

    func secondsLeft() -> Double? {
        guard player.timeControlStatus == .playing, let item = player.currentItem else { return nil }
        let duration = item.duration.seconds
        let current = item.currentTime().seconds
        guard duration.isFinite, current.isFinite else { return nil }
        return max(0, duration - current)
    }

    private func clearItem() {
        player.pause()
        statusObservation?.invalidate()
        statusObservation = nil
        for observer in itemObservers { NotificationCenter.default.removeObserver(observer) }
        itemObservers = []
        started = false
        reportedFirstFrame = false
        player.replaceCurrentItem(with: nil)
    }

    private func statusChanged(_ status: AVPlayerItem.Status, duration: Double, id: Int) {
        switch status {
        case .readyToPlay:
            guard !started else { return }
            send(.ready(id: id, duration: duration.isFinite && duration > 0 ? duration : nil), for: id)
        case .failed:
            send(.failed(id: id), for: id)
        default:
            break
        }
    }

    private func timeControlChanged(playing: Bool) {
        guard playing, started, !reportedFirstFrame else { return }
        reportedFirstFrame = true
        send(.firstFrame(id: loadID), for: loadID)
    }

    private func send(_ event: PlaybackEvent, for id: Int) {
        guard id == loadID else { return }
        onEvent?(event)
    }
}
