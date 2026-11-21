const { addExclusion } = require("./withoutTapAndPay");

describe("withoutTapAndPay", () => {
  const root = 'apply plugin: "expo-root-project"\napply plugin: "com.facebook.react.rootproject"\n';

  it("excludes Tap and Pay from every configuration of every project", () => {
    const out = addExclusion(root);
    expect(out.startsWith(root.trimEnd())).toBe(true);
    expect(out).toMatch(/allprojects \{\s+configurations\.configureEach \{\s+exclude group: "com\.google\.android\.gms", module: "play-services-tapandpay"/);
  });

  it("adds it once, however often prebuild runs", () => {
    const once = addExclusion(root);
    expect(addExclusion(once)).toBe(once);
  });
});
