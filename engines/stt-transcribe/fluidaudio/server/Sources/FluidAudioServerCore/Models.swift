import FluidAudio
import Foundation

/// Model keys the server understands.
///
/// Keys are the FluidAudio Hugging Face repo ids. Dalston's transcribe stage
/// hands engines the registry model's `source`, and the `models/fluidaudio-*`
/// catalog entries set `source` to exactly these strings, so no mapping
/// layer is needed on the Python side.
///
/// Carried over from dalston-mobile's `FluidTranscriptionEngine.swift`
/// (`models`, `speakerModelKey`); keep the two in step when FluidAudio moves.
enum ModelKeys {
    static let parakeetV3 = "FluidInference/parakeet-tdt-0.6b-v3-coreml"
    static let parakeetUltra = "FluidInference/parakeet-ultra-coreml"
    static let speakerDiarization = "FluidInference/speaker-diarization-coreml"

    static let asr: [String: AsrModelVersion] = [
        parakeetV3: .v3,
        parakeetUltra: .ultra,
    ]
    static let asrOrdered = [parakeetV3, parakeetUltra]
    static let diarizers = [speakerDiarization]
    static let defaultAsr = parakeetV3

    /// Parakeet TDT 0.6B v3 languages. FluidAudio's `Language` only filters
    /// tokens by script; it does not force the language.
    static let languages = [
        "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu",
        "it", "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk",
    ]

    static func version(of repo: String) throws -> AsrModelVersion {
        guard let version = asr[repo] else { throw ServerError.unknownModel(repo) }
        return version
    }

    static func language(_ code: String?) -> Language? {
        code.flatMap { Language(rawValue: $0.lowercased()) }
    }
}

enum ServerError: Error, CustomStringConvertible {
    case unknownModel(String)
    case unreadableAudio(String)
    case queueFull
    case missingFile

    var description: String {
        switch self {
        case .unknownModel(let repo): "unknown model \(repo)"
        case .unreadableAudio(let why): "unreadable audio: \(why)"
        case .queueFull: "inference queue is full"
        case .missingFile: "multipart field 'file' is required"
        }
    }
}

// MARK: - Pure conversions (unit-tested without models)

/// A word with timings, independent of FluidAudio's own types so the
/// JSON layer and the tests do not depend on the library.
struct TimedWord: Codable, Equatable, Sendable {
    var text: String
    var start: Double
    var end: Double
    var confidence: Double?
}

/// A speaker segment in Dalston's label scheme (`SPEAKER_00`, ...).
struct SpeakerSegment: Codable, Equatable, Sendable {
    var speaker: String
    var start: Double
    var end: Double
}

enum Conversions {
    /// FluidAudio token timings → words. Thin wrapper so callers never see
    /// `TokenTiming`.
    static func words(from timings: [TokenTiming]) -> [TimedWord] {
        buildWordTimings(from: timings).map {
            TimedWord(text: $0.word, start: $0.startTime, end: $0.endTime, confidence: nil)
        }
    }

    /// FluidAudio labels speakers "S1", "S2"...; Dalston expects
    /// `SPEAKER_00`, `SPEAKER_01`... numbered by first appearance.
    static func renumber(_ segments: [TimedSpeakerSegment]) -> [SpeakerSegment] {
        renumber(segments.map {
            (speaker: $0.speakerId, start: Double($0.startTimeSeconds), end: Double($0.endTimeSeconds))
        })
    }

    /// Label-agnostic core of `renumber` so it can be tested without models.
    static func renumber(_ raw: [(speaker: String, start: Double, end: Double)]) -> [SpeakerSegment] {
        var ids: [String: Int] = [:]
        return raw.map { segment in
            let index = ids[segment.speaker] ?? {
                let next = ids.count
                ids[segment.speaker] = next
                return next
            }()
            return SpeakerSegment(
                speaker: String(format: "SPEAKER_%02d", index),
                start: segment.start,
                end: segment.end
            )
        }
    }
}
