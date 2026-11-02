import Vapor

// MARK: - Wire types

struct HealthResponse: Content {
    var status: String
    var asr_loaded: String?
    var diarizer_loaded: Bool
    var fluidaudio_version: String
    var device: String
    var queue_depth: Int
}

struct CapabilitiesResponse: Content {
    var server: String
    var version: String
    var asr_models: [String]
    var diarizer_models: [String]
    var languages: [String]
}

struct TranscribeForm: Content {
    var file: File?
    var model: String?
    var language: String?
}

struct TranscribeResponse: Content {
    struct Timings: Content {
        var load_model_s: Double
        var transcribe_s: Double
    }
    var model: String
    var duration: Double
    var language: String?
    var text: String
    var words: [TimedWord]
    var timings: Timings
}

struct DiarizeForm: Content {
    var file: File?
    var model: String?
    var num_speakers: Int?
    var min_speakers: Int?
    var max_speakers: Int?
}

struct DiarizeResponse: Content {
    struct Timings: Content {
        var load_model_s: Double
        var diarize_s: Double
    }
    var model: String
    var duration: Double
    var segments: [SpeakerSegment]
    var warnings: [String]
    var timings: Timings
}

struct ErrorBody: Content {
    var error: String
}

// MARK: - Routes

public func routes(_ app: Application) throws {
    app.get("health") { _ async -> HealthResponse in
        HealthResponse(
            status: "ok",
            asr_loaded: await Services.asr.loadedRepo,
            diarizer_loaded: await Services.diarizer.isLoaded,
            fluidaudio_version: ServerDefaults.fluidAudioVersion,
            device: "ane",
            queue_depth: await Services.admission.queueDepth
        )
    }

    app.get("v1", "capabilities") { _ -> CapabilitiesResponse in
        CapabilitiesResponse(
            server: "fluidaudio-server",
            version: ServerDefaults.serverVersion,
            asr_models: ModelKeys.asrOrdered,
            diarizer_models: ModelKeys.diarizers,
            languages: ModelKeys.languages
        )
    }

    app.post("v1", "transcribe") { req async throws -> Response in
        let form = try req.content.decode(TranscribeForm.self)
        guard let file = form.file else { throw ServerError.missingFile }
        let url = try TempAudio.write(file)
        defer { TempAudio.remove(url) }
        let repo = form.model ?? ModelKeys.defaultAsr

        do {
            let outcome = try await Services.admission.run {
                try await Services.asr.transcribe(url: url, repo: repo, language: form.language)
            }
            let body = TranscribeResponse(
                model: outcome.model,
                duration: outcome.duration,
                language: outcome.language,
                text: outcome.text,
                words: outcome.words,
                timings: .init(
                    load_model_s: outcome.loadModelSeconds,
                    transcribe_s: outcome.transcribeSeconds
                )
            )
            return try await body.encodeResponse(for: req)
        } catch {
            return try await errorResponse(error, for: req)
        }
    }

    app.post("v1", "diarize") { req async throws -> Response in
        let form = try req.content.decode(DiarizeForm.self)
        guard let file = form.file else { throw ServerError.missingFile }
        let url = try TempAudio.write(file)
        defer { TempAudio.remove(url) }
        let repo = form.model ?? ModelKeys.speakerDiarization

        do {
            let outcome = try await Services.admission.run {
                try await Services.diarizer.diarize(
                    url: url,
                    repo: repo,
                    numSpeakers: form.num_speakers,
                    minSpeakers: form.min_speakers,
                    maxSpeakers: form.max_speakers
                )
            }
            let body = DiarizeResponse(
                model: outcome.model,
                duration: outcome.duration,
                segments: outcome.segments,
                warnings: outcome.warnings,
                timings: .init(
                    load_model_s: outcome.loadModelSeconds,
                    diarize_s: outcome.diarizeSeconds
                )
            )
            return try await body.encodeResponse(for: req)
        } catch {
            return try await errorResponse(error, for: req)
        }
    }
}

/// Map server errors onto the status codes the adapters expect:
/// 400 for bad input, 503 when the queue is full, 500 for inference errors.
private func errorResponse(_ error: Error, for req: Request) async throws -> Response {
    let status: HTTPResponseStatus
    switch error {
    case ServerError.unknownModel, ServerError.unreadableAudio, ServerError.missingFile:
        status = .badRequest
    case ServerError.queueFull:
        status = .serviceUnavailable
    default:
        req.logger.report(error: error)
        status = .internalServerError
    }
    let response = try await ErrorBody(error: String(describing: error)).encodeResponse(for: req)
    response.status = status
    return response
}

/// Uploaded audio is written to a private temp file so FluidAudio's URL
/// entry points can stream it from disk.
enum TempAudio {
    static func write(_ file: File) throws -> URL {
        let ext = (file.filename as NSString).pathExtension
        let name = UUID().uuidString + (ext.isEmpty ? ".wav" : ".\(ext)")
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("fluidaudio-server", isDirectory: true)
            .appendingPathComponent(name)
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        guard file.data.readableBytes > 0 else {
            throw ServerError.unreadableAudio("empty upload")
        }
        try Data(buffer: file.data).write(to: url, options: .atomic)
        return url
    }

    static func remove(_ url: URL) {
        try? FileManager.default.removeItem(at: url)
    }
}
