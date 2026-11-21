// AsyncStorage has no native module under jest: use the in-memory mock it ships.
jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock"),
);
