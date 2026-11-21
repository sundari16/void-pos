// Which Void POS server this till talks to. It starts at the build's EXPO_PUBLIC_API_BASE
// (config.ts) and can be changed on the pairing screen, so one installed app works at any
// venue: the address is kept in the device's storage and used for every request.

import AsyncStorage from "@react-native-async-storage/async-storage";
import { apiBase } from "./lib/config";

export const SERVER_KEY = "voidpos:server:v1";

let current = apiBase();

export function serverBase(): string {
  return current;
}

/** A typed server address as the till will use it, or why it can't be one. */
export function parseServer(input: string): { base: string } | { error: string } {
  let text = input.trim().replace(/\/+$/, "");
  if (!text) return { error: "Enter the server's address, like http://192.168.1.20:8000." };
  if (!/^https?:\/\//i.test(text)) text = `http://${text}`;
  const m = /^(https?):\/\/([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(:\d{1,5})?(\/[^\s?#]*)?$/i.exec(text);
  if (!m) return { error: "That doesn't look like a server address." };
  const port = m[3] ? Number(m[3].slice(1)) : null;
  if (port !== null && (port < 1 || port > 65535)) return { error: "That port isn't valid." };
  return { base: `${m[1].toLowerCase()}://${m[2]}${m[3] ?? ""}${m[4] ?? ""}` };
}

export async function loadServer(): Promise<string> {
  try {
    const saved = await AsyncStorage.getItem(SERVER_KEY);
    if (saved) current = saved;
  } catch {
    // Keep the build's default.
  }
  return current;
}

export async function saveServer(base: string): Promise<void> {
  current = base;
  try {
    await AsyncStorage.setItem(SERVER_KEY, base);
  } catch {
    // Used for now; it just won't survive a restart.
  }
}
