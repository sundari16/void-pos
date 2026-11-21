// Config plugin: keep Google's restricted Tap and Pay SDK out of the Android build.
//
// @stripe/stripe-react-native declares Stripe's Google Wallet push-provisioning SDK
// (com.stripe:stripe-android-issuing-push-provisioning) as compileOnly, so it is never
// packaged, and reaches Tap and Pay only by reflection. That SDK's runtime dependency,
// com.google.android.gms:play-services-tapandpay, is Google's restricted wallet SDK and isn't
// on any public Maven repository, so a release build fails resolving it for lint
// (lintVitalAnalyzeRelease). The till never provisions cards into a wallet, so nothing needs
// it: excluding it everywhere leaves the APK exactly as it would be, and lint runs in full.

const { withProjectBuildGradle } = require("expo/config-plugins");

const MARKER = "// @void-pos/without-tapandpay";

const BLOCK = `
${MARKER}: Google's restricted wallet SDK (not on public Maven) is
// only a runtime dependency of Stripe's compileOnly push-provisioning SDK; see
// mobile/plugins/withoutTapAndPay.js.
allprojects {
  configurations.configureEach {
    exclude group: "com.google.android.gms", module: "play-services-tapandpay"
  }
}
`;

/** The root build.gradle with the exclusion added once. */
function addExclusion(contents) {
  return contents.includes(MARKER) ? contents : `${contents.trimEnd()}\n${BLOCK}`;
}

function withoutTapAndPay(config) {
  return withProjectBuildGradle(config, (cfg) => {
    if (cfg.modResults.language !== "groovy") {
      throw new Error("withoutTapAndPay expects android/build.gradle in Groovy");
    }
    cfg.modResults.contents = addExclusion(cfg.modResults.contents);
    return cfg;
  });
}

module.exports = withoutTapAndPay;
module.exports.addExclusion = addExclusion;
