// The till's identity and counters live in the device's AsyncStorage, so a paired till
// survives an app restart. The counter arithmetic is pure (till.ts); this module only reads
// and writes the serialized Till. Every call is wrapped so a storage failure degrades to
// "not paired" rather than crashing the till.

import AsyncStorage from "@react-native-async-storage/async-storage";
import { parseTill, serializeTill, TILL_KEY, type Till } from "./till";

export async function loadTill(): Promise<Till | null> {
  try {
    return parseTill(await AsyncStorage.getItem(TILL_KEY));
  } catch {
    return null;
  }
}

export async function saveTill(till: Till): Promise<void> {
  try {
    await AsyncStorage.setItem(TILL_KEY, serializeTill(till));
  } catch {
    // A write failure doesn't stop the till working now; it just won't survive a restart.
  }
}

export async function clearTill(): Promise<void> {
  try {
    await AsyncStorage.removeItem(TILL_KEY);
  } catch {
    // Nothing to do: the till is being unpaired anyway.
  }
}
