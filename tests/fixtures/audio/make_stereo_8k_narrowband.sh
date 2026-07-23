#!/bin/zsh
# Generate the M92 narrowband stereo test fixture.
# Shape mirrors the incident call: ch1 (caller) has three turns separated by
# line-noise gaps (0s, ~18s, ~26s); ch0 (agent) speaks ~3-16s and ~21-24s.
# Croatian TTS (macOS 'say' voice Lana, hr_HR), low-passed at 2 kHz,
# resampled to 8 kHz — mimicking the aggressive telephony low-pass.
set -euo pipefail
DIR=$(mktemp -d)
OUT="$1"

say -v Lana -o "$DIR/c1a.aiff" "Dobar dan, ovdje Ivana Matković iz Splita."
say -v Lana -o "$DIR/c1b.aiff" "Greška fiskalizacije se pojavila u trenutku izdavanja računa."
say -v Lana -o "$DIR/c1c.aiff" "Račun nije evidentiran, molim provjerite poslovni prostor i oznaku."
say -v Lana -o "$DIR/c0a.aiff" "Dobar dan, hvala na pozivu. Provjerit ću postavke fiskalizacije za vaš poslovni prostor. Molim vas recite mi jedinstveni identifikator računa i zaštitni kod izdavatelja, pa ću odmah pogledati status u sustavu Porezne uprave."
say -v Lana -o "$DIR/c0b.aiff" "U redu, vidim grešku, odmah je ispravljam."

ffmpeg -y -hide_banner -loglevel error \
  -i "$DIR/c1a.aiff" -i "$DIR/c1b.aiff" -i "$DIR/c1c.aiff" \
  -i "$DIR/c0a.aiff" -i "$DIR/c0b.aiff" \
  -f lavfi -t 30 -i "anoisesrc=colour=pink:amplitude=0.012:seed=92" \
  -f lavfi -t 30 -i "anullsrc=r=8000:cl=mono" \
  -filter_complex "\
[0:a]aresample=8000,pan=mono|c0=c0[c1a];\
[1:a]aresample=8000,pan=mono|c0=c0,adelay=18000[c1b];\
[2:a]aresample=8000,pan=mono|c0=c0,adelay=26000[c1c];\
[3:a]aresample=8000,pan=mono|c0=c0,adelay=3000[c0a];\
[4:a]aresample=8000,pan=mono|c0=c0,adelay=21000[c0b];\
[5:a]aresample=8000,lowpass=f=500[noise];\
[noise][c1a][c1b][c1c]amix=inputs=4:duration=longest:normalize=0,lowpass=f=2000[ch1];\
[6:a][c0a][c0b]amix=inputs=3:duration=longest:normalize=0,lowpass=f=2000[ch0];\
[ch0][ch1]join=inputs=2:channel_layout=stereo[out]" \
  -map "[out]" -t 30 -ar 8000 -c:a pcm_s16le "$OUT"

rm -rf "$DIR"
