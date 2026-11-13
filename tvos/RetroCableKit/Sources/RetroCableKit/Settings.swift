import Foundation

/// The year and channel, kept in UserDefaults under the browser's localStorage keys.
public final class Settings {
    public static let yearKey = "rc.year"
    public static let channelKey = "rc.channel"

    private let defaults: UserDefaults

    public init(_ defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    /// 1990-2010, or 0 for All Years. A missing or invalid stored year reads as 1998; setting an
    /// invalid year is ignored.
    public var year: Int {
        get {
            let raw = defaults.object(forKey: Self.yearKey)
            if let number = raw as? Int { return Years.valid(number) ?? Years.defaultYear }
            return Years.parse(raw as? String) ?? Years.defaultYear
        }
        set {
            guard let year = Years.valid(newValue) else { return }
            defaults.set(year, forKey: Self.yearKey)
        }
    }

    /// The channel the viewer last tuned on purpose. A fallback to another channel (the stored one
    /// is off the dial) doesn't change it.
    public var channel: String? {
        get { defaults.string(forKey: Self.channelKey) }
        set { defaults.set(newValue, forKey: Self.channelKey) }
    }
}
