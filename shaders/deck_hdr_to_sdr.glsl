// libplacebo user shader for KMS captures of Deck OLED's own panel in HDR.
// The panel runs HDR as gamma 2.2 up to 1000 nits (gamescope's display profile,
// valve.steamdeck.oled.lua). libplacebo hands over gamma 2.2 values already
// converted to BT.709 primaries, where 1.0 is the panel's 1000 nits. This makes
// SDR with white at white_nits, but instead of clipping everything brighter it
// rolls the highlights from white_nits up to peak_nits off with the BT.2390 curve
// (in PQ), applied to the brightest channel so hues and saturation stay as they
// are. The plugin writes a copy per recording with peak_nits (the game's HDR peak
// brightness setting) and white_nits (1.5 x its paper white) filled in, and on
// other handhelds whose panels gamescope also drives as gamma 2.2 (Zotac Zone,
// OneXPlayer F1 OLED) panel_nits from gamescope's profile for that panel.
//!HOOK OUTPUT
//!BIND HOOKED
//!DESC Screen Recorder: Deck OLED HDR to SDR with soft highlights

const float panel_nits = 1000.0;
const float peak_nits = 1000.0;
const float white_nits = 304.5;

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

// BT.2390 EETF from peak_nits down to white_nits; unchanged below the knee.
float roll_off(float nits) {
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
    vec3 nits = min(pow(max(color.rgb, 0.0), vec3(2.2)) * panel_nits, vec3(peak_nits));
    float brightest = max(max(nits.r, nits.g), nits.b);
    if (brightest > 0.0) nits *= roll_off(brightest) / brightest;
    color.rgb = pow(clamp(nits / white_nits, 0.0, 1.0), vec3(1.0 / 2.2));
    return color;
}
