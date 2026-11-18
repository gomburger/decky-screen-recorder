// libplacebo user shader for KMS captures of an external HDR10 display.
// libplacebo hands over PQ values already converted to BT.709 primaries (its
// output is set to PQ, BT.709, full range, clipped). This turns them into gamma
// 2.2 SDR with white at white_nits, clipping brighter values, the way gamescope
// does for its PipeWire stream. The plugin writes a copy per recording with
// white_nits set: 480 for an HDR game (measured 2026-11-18 on Deck OLED and an
// external monitor alike), Steam's SDR content brightness for SDR content.
//!HOOK OUTPUT
//!BIND HOOKED
//!DESC Screen Recorder: PQ to gamma 2.2 SDR
vec4 hook() {
    vec4 color = HOOKED_texOff(0);
    const float m1 = 0.1593017578125;
    const float m2 = 78.84375;
    const float c1 = 0.8359375;
    const float c2 = 18.8515625;
    const float c3 = 18.6875;
    const float white_nits = 480.0;
    vec3 e = pow(max(color.rgb, 0.0), vec3(1.0 / m2));
    vec3 nits = pow(max(e - c1, 0.0) / (c2 - c3 * e), vec3(1.0 / m1)) * 10000.0;
    color.rgb = pow(clamp(nits / white_nits, 0.0, 1.0), vec3(1.0 / 2.2));
    return color;
}
