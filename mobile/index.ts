// The Web Crypto polyfill must load before anything that mints a ULID (src/lib/ulid.ts uses
// globalThis.crypto.getRandomValues, which React Native doesn't provide on its own), so it is
// the very first import in the whole app.
import "react-native-get-random-values";

import { registerRootComponent } from "expo";

import App from "./App";

// registerRootComponent calls AppRegistry.registerComponent('main', () => App); it also sets
// the environment up for Expo Go or a native build.
registerRootComponent(App);
