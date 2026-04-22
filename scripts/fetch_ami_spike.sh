#!/usr/bin/env bash
# Fetch AMI Meeting Corpus test-split files + reference RTTMs for the M89
# AITune spike. Throwaway — run on the GPU host before `spike_aitune.py`.
#
# Downloads ~6 headset-mix WAVs (~300MB total) into tests/audio/ami/ and
# reference RTTMs into tests/audio/ami/rttm/. Both directories are gitignored.
#
# AMI is CC-BY 4.0. URLs below match the AMICorpusMirror layout that has been
# stable for years; if a 404 surfaces, check https://groups.inf.ed.ac.uk/ami/
# for a revised path. Reference RTTMs come from BUTSpeechFIT/AMI-diarization-setup.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AUDIO_DIR="${REPO_ROOT}/tests/audio/ami"
RTTM_DIR="${AUDIO_DIR}/rttm"

mkdir -p "${AUDIO_DIR}" "${RTTM_DIR}"

# Primary throughput set (5) + long-tail stress file (1)
MEETINGS=(ES2004a ES2014a IS1009a TS3003a TS3007a ES2004b)

AMI_BASE="https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus"
RTTM_BASE="https://raw.githubusercontent.com/BUTSpeechFIT/AMI-diarization-setup/main/only_words/rttms/test"

for m in "${MEETINGS[@]}"; do
    wav="${AUDIO_DIR}/${m}.Mix-Headset.wav"
    if [[ ! -f "${wav}" ]]; then
        echo "Fetching audio: ${m}"
        curl -fsSL -o "${wav}" "${AMI_BASE}/${m}/audio/${m}.Mix-Headset.wav"
    else
        echo "Audio already present: ${m}"
    fi

    rttm="${RTTM_DIR}/${m}.rttm"
    if [[ ! -f "${rttm}" ]]; then
        echo "Fetching reference RTTM: ${m}"
        curl -fsSL -o "${rttm}" "${RTTM_BASE}/${m}.rttm"
    else
        echo "RTTM already present: ${m}"
    fi
done

echo "Done. ${#MEETINGS[@]} meetings in ${AUDIO_DIR}/"
