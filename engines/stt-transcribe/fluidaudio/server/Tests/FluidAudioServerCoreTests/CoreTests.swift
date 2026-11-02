import Testing

@testable import FluidAudioServerCore

// Pure logic only: nothing here downloads or loads a model, so `swift test`
// runs on any Apple Silicon Mac in seconds.

@Suite struct ConversionsTests {
    @Test func renumbersSpeakersByFirstAppearance() {
        let raw: [(speaker: String, start: Double, end: Double)] = [
            ("S2", 0.0, 1.0),
            ("S1", 1.0, 2.0),
            ("S2", 2.0, 3.0),
            ("S7", 3.0, 4.0),
        ]
        let segments = Conversions.renumber(raw)
        #expect(segments.map(\.speaker) == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00", "SPEAKER_02"])
        #expect(segments[1].start == 1.0 && segments[1].end == 2.0)
    }

    @Test func emptyInputStaysEmpty() {
        let raw: [(speaker: String, start: Double, end: Double)] = []
        #expect(Conversions.renumber(raw).isEmpty)
    }
}

@Suite struct ModelKeysTests {
    @Test func knownRepoIdsResolve() throws {
        _ = try ModelKeys.version(of: ModelKeys.parakeetV3)
        _ = try ModelKeys.version(of: ModelKeys.parakeetUltra)
    }

    @Test func unknownRepoIdIsRejected() {
        #expect(throws: ServerError.self) {
            try ModelKeys.version(of: "FluidInference/not-a-model")
        }
    }

    @Test func languageCodesAreCaseInsensitive() {
        #expect(ModelKeys.language("EN") != nil)
        #expect(ModelKeys.language(nil) == nil)
    }

    @Test func catalogListsMatchTheModelMap() {
        #expect(Set(ModelKeys.asrOrdered) == Set(ModelKeys.asr.keys))
        #expect(ModelKeys.asrOrdered.contains(ModelKeys.defaultAsr))
    }
}

@Suite struct AdmissionTests {
    @Test func serializesWork() async throws {
        let admission = Admission(maxQueued: 2)
        let counter = Counter()

        try await withThrowingTaskGroup(of: Void.self) { group in
            for _ in 0..<3 {
                group.addTask {
                    try await admission.run {
                        await counter.enter()
                        try await Task.sleep(for: .milliseconds(20))
                        await counter.leave()
                    }
                }
            }
            try await group.waitForAll()
        }
        #expect(await counter.maxConcurrent == 1)
        #expect(await counter.total == 3)
        #expect(await admission.queueDepth == 0)
    }

    @Test func rejectsWhenQueueIsFull() async throws {
        let admission = Admission(maxQueued: 1)
        let gate = Gate()

        // Occupy the slot and the one queue position.
        let running = Task {
            try await admission.run { await gate.wait() }
        }
        let queued = Task {
            try await admission.run { await gate.wait() }
        }
        // Let both tasks reach the actor before probing.
        try await Task.sleep(for: .milliseconds(50))
        #expect(await admission.queueDepth == 2)

        await #expect(throws: ServerError.self) {
            try await admission.run { }
        }

        await gate.open()
        try await running.value
        try await queued.value
        #expect(await admission.queueDepth == 0)
    }
}

/// Tracks how many bodies run at once.
private actor Counter {
    private(set) var current = 0
    private(set) var maxConcurrent = 0
    private(set) var total = 0

    func enter() {
        current += 1
        total += 1
        maxConcurrent = max(maxConcurrent, current)
    }

    func leave() { current -= 1 }
}

/// Lets a test hold bodies inside `Admission.run` until it says so.
private actor Gate {
    private var opened = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        if opened { return }
        await withCheckedContinuation { waiters.append($0) }
    }

    func open() {
        opened = true
        waiters.forEach { $0.resume() }
        waiters.removeAll()
    }
}
