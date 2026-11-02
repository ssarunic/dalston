import FluidAudio
import Foundation

/// Result of one transcription, in the server's own JSON shape.
struct TranscribeOutcome: Sendable {
    var model: String
    var duration: Double
    var language: String?
    var text: String
    var words: [TimedWord]
    var loadModelSeconds: Double
    var transcribeSeconds: Double
}

/// Owns the loaded Parakeet model. One model resident at a time; asking for
/// a different repo swaps it. Non-Sendable FluidAudio objects never leave
/// this actor.
public actor ASRService {
    public init() {}

    private var loaded: (repo: String, models: AsrModels, manager: AsrManager)?

    public var loadedRepo: String? { loaded?.repo }

    /// Load (or switch to) `repo`. `progress` gets the download fraction.
    public func load(repo: String, progress: @escaping @Sendable (Double) -> Void) async throws {
        if loaded?.repo == repo { return }
        let version = try ModelKeys.version(of: repo)
        loaded = nil
        // int8-v2 avoids token corruption in the default v3 encoder (FluidAudio
        // issue #760); other versions ship one encoder and ignore the setting.
        // Same choice as dalston-mobile.
        let models = try await AsrModels.downloadAndLoad(
            version: version,
            encoderPrecision: .int8V2,
            progressHandler: { progress($0.fractionCompleted) }
        )
        let manager = AsrManager(config: .default)
        try await manager.loadModels(models)
        loaded = (repo, models, manager)
    }

    func transcribe(url: URL, repo: String, language: String?) async throws -> TranscribeOutcome {
        let loadStart = Date()
        try await load(repo: repo) { _ in }
        let loadSeconds = Date().timeIntervalSince(loadStart)
        guard let loaded else { throw ServerError.unknownModel(repo) }

        let start = Date()
        // The URL entry point streams long files from disk instead of holding
        // the whole recording in memory (dalston-mobile: ~2 GB peak otherwise).
        var state = TdtDecoderState.make(decoderLayers: await loaded.manager.decoderLayerCount)
        let result = try await loaded.manager.transcribe(
            url, decoderState: &state, language: ModelKeys.language(language)
        )
        let seconds = Date().timeIntervalSince(start)

        return TranscribeOutcome(
            model: repo,
            duration: result.duration,
            // Parakeet v3 does not report the language it decoded; the adapter
            // falls back to the requested one.
            language: nil,
            text: result.text.trimmingCharacters(in: .whitespacesAndNewlines),
            words: Conversions.words(from: result.tokenTimings ?? []),
            loadModelSeconds: loadSeconds,
            transcribeSeconds: seconds
        )
    }
}
