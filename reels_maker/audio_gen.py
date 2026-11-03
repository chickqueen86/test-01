#!/usr/bin/env python3
"""배경음악·효과음 합성 (저작권 걱정 없는 자체 생성 소리).

레퍼런스 분석(team/04_사운드.md) 기준
- BGM: 드럼 거의 없는 로파이·칠 패드, G# 단조 부근, 약 115 BPM, 베이스는 후반부터 등장,
       LP 잡음(바이닐 크래클)으로 로파이 질감
- 효과음: 레퍼런스는 촬영 현장음(튀김·칼질)을 컷 시작 0.1~0.3초 안에 둠, 전환 효과음 없음(하드컷)
         → 원본에 소리가 없는 클립은 굽는 소리(지글지글)·써는 소리(바삭)를 합성해 같은 위치에 넣음
"""
import wave
import numpy as np

SR = 48000


def _write_wav(path, x):
    x = np.clip(x, -1, 1)
    st = (np.stack([x, x], 1) * 32767).astype(np.int16) if x.ndim == 1 else (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(st.tobytes())


def _lowpass(x, cutoff):
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x); acc = 0.0
    for i in range(len(x)):          # 1차 IIR (짧은 신호용)
        acc = (1 - a) * x[i] + a * acc; y[i] = acc
    return y


def _lp_fft(x, cutoff, hi=None):
    X = np.fft.rfft(x); f = np.fft.rfftfreq(len(x), 1 / SR)
    m = 1 / (1 + (f / cutoff) ** 4)
    if hi:
        m *= 1 - 1 / (1 + (f / hi) ** 4)
    return np.fft.irfft(X * m, len(x))


def _note(n):        # MIDI → Hz
    return 440.0 * 2 ** ((n - 69) / 12)


def make_bgm(path, dur, bpm=115, seed=3):
    """G# 단조 로파이 패드: G#m - E - B - F# 진행, 부드러운 패드 + 플럭 아르페지오 + 후반 베이스 + 바이닐 잡음."""
    rng = np.random.default_rng(seed)
    n = int(dur * SR); t = np.arange(n) / SR
    beat = 60 / bpm; bar = beat * 4
    chords = [[56, 59, 63, 68], [52, 56, 59, 64], [59, 63, 66, 71], [54, 58, 61, 66]]   # G#m E B F#
    roots = [44, 40, 47, 42]
    pad = np.zeros(n); arp = np.zeros(n); bass = np.zeros(n)
    nb = int(np.ceil(dur / bar)) + 1
    for b in range(nb):
        ch = chords[b % 4]; s0 = int(b * bar * SR); s1 = min(int((b + 1) * bar * SR), n)
        if s0 >= n:
            break
        tt = np.arange(s1 - s0) / SR
        env = np.minimum(1, tt / 0.6) * np.minimum(1, (bar - tt) / 0.4)
        for k, m in enumerate(ch):
            f = _note(m)
            for det in (-0.12, 0.12):                       # 살짝 어긋난 두 음 → 따뜻한 패드
                pad[s0:s1] += np.sin(2 * np.pi * f * (1 + det / 100) * tt + k) * env * 0.05
        for i in range(8):                                  # 8분음 아르페지오 (부드러운 플럭)
            a0 = s0 + int(i * beat / 2 * SR)
            if a0 >= n:
                break
            L = min(int(0.5 * SR), n - a0); tt2 = np.arange(L) / SR
            f = _note(ch[[0, 2, 1, 3, 2, 1, 3, 2][i]] + 12)
            arp[a0:a0 + L] += np.sin(2 * np.pi * f * tt2) * np.exp(-tt2 * 7) * 0.05
        f = _note(roots[b % 4])
        bass[s0:s1] += (np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(4 * np.pi * f * tt)) * env * 0.11
    bass_in = 0.82 * dur                                    # 레퍼런스: 베이스는 후반(약 82% 지점)부터
    bass *= np.clip((t - bass_in) / 1.5, 0, 1)
    kick = np.zeros(n)                                      # 아주 약한 킥 (드럼 '거의' 없음)
    for k in np.arange(0, dur, beat * 2):
        a0 = int(k * SR); L = min(int(0.25 * SR), n - a0)
        if L <= 0:
            break
        tt3 = np.arange(L) / SR
        kick[a0:a0 + L] += np.sin(2 * np.pi * (55 + 60 * np.exp(-tt3 * 30)) * tt3) * np.exp(-tt3 * 14) * 0.12
    crackle = np.zeros(n)                                   # 바이닐 잡음
    pops = rng.integers(0, n, int(dur * 25))
    crackle[pops] = rng.uniform(-0.25, 0.25, len(pops))
    crackle = _lp_fft(crackle + rng.normal(0, 0.004, n), 6000, 300)
    mix = _lp_fft(pad + arp, 3500) + bass + kick + crackle
    mix *= np.minimum(1, t / 1.0) * np.minimum(1, (dur - t) / 1.5)
    mix /= max(np.abs(mix).max(), 1e-6) / 0.8
    _write_wav(path, mix)
    return path


def sizzle(dur, rng):
    """굽는 소리 (지글지글): 고역 잡음 + 무작위 기름 튀는 소리."""
    n = int(dur * SR)
    x = _lp_fft(rng.normal(0, 1, n), 9000, 2500) * 0.25
    pops = np.zeros(n); idx = rng.integers(0, n, int(dur * 60))
    pops[idx] = rng.uniform(0.3, 1, len(idx)) * rng.choice([-1, 1], len(idx))
    x += _lp_fft(pops, 12000, 3000) * 0.9
    tt = np.arange(n) / SR
    x *= (0.75 + 0.25 * np.sin(2 * np.pi * 3.1 * tt)) * np.minimum(1, tt / 0.03) * np.minimum(1, (dur - tt) / 0.08)
    return x / max(np.abs(x).max(), 1e-6)


def crunch(rng, dur=0.35):
    """써는 소리 (바삭): 짧은 잡음 덩어리 여러 개."""
    n = int(dur * SR); x = np.zeros(n)
    for k in range(rng.integers(4, 8)):
        a0 = int(rng.uniform(0, dur * 0.7) * SR); L = int(rng.uniform(0.01, 0.035) * SR)
        if a0 + L > n:
            continue
        x[a0:a0 + L] += rng.normal(0, 1, L) * np.exp(-np.arange(L) / (L / 3)) * rng.uniform(0.4, 1)
    x = _lp_fft(x, 7000, 1200)
    return x / max(np.abs(x).max(), 1e-6)


def make_sfx(path, scenes, total, seed=11):
    """컷 목록을 보고 효과음 트랙 생성.
    scenes: [{start(영상 내 시작초), dur, kind}] kind = 'sizzle' | 'crunch' | None
    레퍼런스처럼 첫 소리가 컷 시작 0.1~0.3초 안에 오도록 배치."""
    rng = np.random.default_rng(seed)
    n = int(total * SR) + SR; out = np.zeros(n)
    for sc in scenes:
        kind = sc.get("kind")
        if not kind:
            continue
        a0 = int((sc["start"] + rng.uniform(0.05, 0.15)) * SR)
        if kind == "sizzle":
            x = sizzle(max(sc["dur"] - 0.05, 0.2), rng) * 0.55
        elif kind == "crunch":
            x = np.concatenate([crunch(rng) for _ in range(max(1, int(sc["dur"] / 0.45)))]) * 0.7
        else:
            continue
        L = min(len(x), n - a0)
        if L > 0:
            out[a0:a0 + L] += x[:L]
    _write_wav(path, out[:int(total * SR)])
    return path
