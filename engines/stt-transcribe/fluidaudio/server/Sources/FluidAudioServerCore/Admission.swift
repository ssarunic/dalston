import Foundation

/// Serializes inference and bounds the waiting line.
///
/// The Neural Engine does not benefit from concurrent requests, and the
/// Dalston adapters are single-task runners, so at most one transcribe and
/// one diarize request should ever be in flight. One runs; up to
/// `maxQueued` wait; anything beyond that gets `ServerError.queueFull`,
/// which the HTTP layer turns into 503. The adapters treat that as a task
/// failure rather than retry-looping against the task deadline.
public actor Admission {
    private let maxQueued: Int
    private var running = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    public init(maxQueued: Int) {
        self.maxQueued = maxQueued
    }

    public var queueDepth: Int { waiters.count + (running ? 1 : 0) }

    /// Run `body` once the slot is free. Throws `queueFull` immediately if
    /// too many requests are already waiting.
    func run<T: Sendable>(_ body: @Sendable () async throws -> T) async throws -> T {
        try acquire()
        if running {
            await withCheckedContinuation { continuation in
                waiters.append(continuation)
            }
        }
        running = true
        defer { release() }
        return try await body()
    }

    private func acquire() throws {
        // `running` holds one request; `waiters` holds the rest.
        if running && waiters.count >= maxQueued {
            throw ServerError.queueFull
        }
    }

    private func release() {
        if waiters.isEmpty {
            running = false
        } else {
            // Hand the slot straight to the next waiter; `running` stays true.
            waiters.removeFirst().resume()
        }
    }
}
