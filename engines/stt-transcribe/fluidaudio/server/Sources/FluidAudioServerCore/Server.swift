import Vapor

/// Process-wide services. Actors, so they are Sendable and can be reached
/// from any request handler.
public enum Services {
    public static let admission = Admission(maxQueued: ServerDefaults.maxQueued)
    public static let asr = ASRService()
    public static let diarizer = DiarizeService()
}

public enum ServerDefaults {
    public static let host = "127.0.0.1"
    public static let port = 9200
    /// Whole recordings arrive as one multipart body: 16 kHz PCM WAV is
    /// ~115 MB per hour, so a 2-hour file fits comfortably under this.
    public static let maxBodySize: ByteCount = "4gb"
    /// One waiting request per stage (transcribe, diarize). The adapters are
    /// single-task runners, so more than this means something is retrying
    /// when it should not be.
    public static let maxQueued = 2
    /// Keep in step with the exact version pinned in Package.swift.
    public static let fluidAudioVersion = "0.17.7"
    public static let serverVersion = "1.0.0"
}
