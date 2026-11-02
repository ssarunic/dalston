import FluidAudio
import Foundation

struct DiarizeOutcome: Sendable {
    var model: String
    var duration: Double
    var segments: [SpeakerSegment]
    var warnings: [String]
    var loadModelSeconds: Double
    var diarizeSeconds: Double
}

/// Owns the pyannote community-1 Core ML models. The models load once; a
/// fresh `OfflineDiarizerManager` is built per request because the speaker
/// count lives in its config (same approach as dalston-mobile).
public actor DiarizeService {
    public init() {}

    /// `OfflineDiarizerManager` is a non-Sendable class whose async methods
    /// hop off the actor; only this actor ever holds one.
    private final class ManagerBox: @unchecked Sendable {
        let manager: OfflineDiarizerManager
        init(_ manager: OfflineDiarizerManager) { self.manager = manager }
    }

    private var models: OfflineDiarizerModels?

    public var isLoaded: Bool { models != nil }

    public func loadModels() async throws {
        if models == nil {
            models = try await OfflineDiarizerModels.load()
        }
    }

    func diarize(
        url: URL,
        repo: String,
        numSpeakers: Int?,
        minSpeakers: Int?,
        maxSpeakers: Int?
    ) async throws -> DiarizeOutcome {
        guard ModelKeys.diarizers.contains(repo) else { throw ServerError.unknownModel(repo) }

        let loadStart = Date()
        try await loadModels()
        let loadSeconds = Date().timeIntervalSince(loadStart)
        guard let models else { throw ServerError.unknownModel(repo) }

        var warnings: [String] = []
        if minSpeakers != nil || maxSpeakers != nil {
            // FluidAudio exposes an exact count, not a range.
            warnings.append("min_speakers/max_speakers are not supported by FluidAudio and were ignored")
        }

        var config = OfflineDiarizerConfig.default
        config.clustering.numSpeakers = numSpeakers
        let manager = OfflineDiarizerManager(config: config)
        manager.initialize(models: models)
        let box = ManagerBox(manager)

        let start = Date()
        // URL entry point: streams from disk for long files.
        let result = try await box.manager.process(url) { _, _ in }
        let seconds = Date().timeIntervalSince(start)

        let segments = Conversions.renumber(result.segments)
        let duration = segments.map(\.end).max() ?? 0
        return DiarizeOutcome(
            model: repo,
            duration: duration,
            segments: segments,
            warnings: warnings,
            loadModelSeconds: loadSeconds,
            diarizeSeconds: seconds
        )
    }
}
