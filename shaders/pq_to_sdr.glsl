// libplacebo user shader for KMS captures of an external HDR10 display.
// libplacebo hands over PQ values already converted to BT.709 primaries (its
// output is set to PQ, BT.709, full range, clipped). This turns them into gamma
// 2.2 SDR with white at white_nits; brighter values up to peak_nits roll off with
// the BT.2390 curve (in PQ) on the brightest channel, so hues and saturation stay.
// The plugin writes a copy per recording: for an HDR game peak_nits is the game's
// HDR peak brightness setting and white_nits 1.5 x its paper white; for SDR
// content both are Steam's SDR content brightness, which clips at that white.
// This template's values (480 and 480) are gamescope's own PipeWire conversion,
// measured 2026-11-18 on Deck OLED and an external monitor alike.
//!HOOK OUTPUT
//!BIND HOOKED
//!DESC Screen Recorder: PQ to gamma 2.2 SDR

const float peak_nits = 480.0;
const float white_nits = 480.0;

const float m1 = 0.1593017578125;
const float m2 = 78.84375;
const float c1 = 0.8359375;
const float c2 = 18.8515625;
const float c3 = 18.6875;

float pq_from_nits(float nits) {
    float y = pow(max(nits, 0.0) / 10000.0, m1);
    return pow((c1 + c2 * y) / (1.0 + c3 * y), m2);
}

float nits_from_pq(float e) {
    float p = pow(max(e, 0.0), 1.0 / m2);
    return pow(max(p - c1, 0.0) / (c2 - c3 * p), 1.0 / m1) * 10000.0;
}

// BT.2390 EETF from peak_nits down to white_nits; unchanged below the knee, and
// nothing to do when the peak is not above white.
float roll_off(float nits) {
    if (peak_nits <= white_nits) return nits;
    float source = pq_from_nits(peak_nits);
    float e = pq_from_nits(nits) / source;
    float target = pq_from_nits(white_nits) / source;
    float knee = 1.5 * target - 0.5;
    if (e > knee) {
        float t = (min(e, 1.0) - knee) / (1.0 - knee);
        float t2 = t * t;
        float t3 = t2 * t;
        e = (2.0 * t3 - 3.0 * t2 + 1.0) * knee + (t3 - 2.0 * t2 + t) * (1.0 - knee)
            + (-2.0 * t3 + 3.0 * t2) * target;
    }
    return nits_from_pq(e * source);
}

vec4 hook() {
    vec4 color = HOOKED_texOff(0);
    vec3 e = pow(max(color.rgb, 0.0), vec3(1.0 / m2));
    vec3 nits = pow(max(e - c1, 0.0) / (c2 - c3 * e), vec3(1.0 / m1)) * 10000.0;
    nits = min(nits, vec3(max(peak_nits, white_nits)));
    float brightest = max(max(nits.r, nits.g), nits.b);
    if (brightest > 0.0) nits *= roll_off(brightest) / brightest;
    color.rgb = pow(clamp(nits / white_nits, 0.0, 1.0), vec3(1.0 / 2.2));
    return color;
}
