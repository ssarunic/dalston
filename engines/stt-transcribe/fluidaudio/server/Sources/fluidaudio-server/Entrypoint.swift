import FluidAudioServerCore
import Vapor

/// fluidaudio-server: a loopback HTTP sidecar around FluidAudio.
///
/// The Dalston Python adapters (`engines/stt-transcribe/fluidaudio`,
/// `engines/stt-diarize/fluidaudio`) POST audio here and convert the JSON
/// into Dalston's typed responses. This process owns the Core ML models and
/// serializes Neural Engine work across both stages.
///
/// Configuration (environment variables):
///   FLUIDAUDIO_HOST              bind address (default 127.0.0.1; keep loopback)
///   FLUIDAUDIO_PORT              TCP port (default 9200)
///   FLUIDAUDIO_PRELOAD_ASR       repo id to load at startup (optional)
///   FLUIDAUDIO_PRELOAD_DIARIZER  "1" to load the diarizer at startup
@main
enum Entrypoint {
    static func main() async throws {
        var env = try Environment.detect()
        try LoggingSystem.bootstrap(from: &env)

        let app = try await Application.make(env)
        do {
            try configure(app)
            try await preload(app.logger)
            try await app.execute()
        } catch {
            app.logger.report(error: error)
            try? await app.asyncShutdown()
            throw error
        }
        try await app.asyncShutdown()
    }

    /// Routes, limits and bind address.
    static func configure(_ app: Application) throws {
        let host = Environment.get("FLUIDAUDIO_HOST") ?? ServerDefaults.host
        let port = Environment.get("FLUIDAUDIO_PORT").flatMap(Int.init) ?? ServerDefaults.port
        app.http.server.configuration.hostname = host
        app.http.server.configuration.port = port
        app.routes.defaultMaxBodySize = ServerDefaults.maxBodySize
        try routes(app)
        app.logger.info("fluidaudio-server configured", metadata: [
            "host": .string(host),
            "port": .string(String(port)),
            "fluidaudio": .string(ServerDefaults.fluidAudioVersion),
        ])
    }

    /// Optional warm start so the first task does not pay the download and
    /// Core ML compile cost.
    static func preload(_ logger: Logger) async throws {
        if let repo = Environment.get("FLUIDAUDIO_PRELOAD_ASR") {
            logger.info("preloading ASR model", metadata: ["model": .string(repo)])
            try await Services.asr.load(repo: repo) { _ in }
        }
        if Environment.get("FLUIDAUDIO_PRELOAD_DIARIZER") == "1" {
            logger.info("preloading diarizer models")
            try await Services.diarizer.loadModels()
        }
    }
}
