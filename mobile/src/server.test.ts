import AsyncStorage from "@react-native-async-storage/async-storage";
import { loadServer, parseServer, saveServer, SERVER_KEY, serverBase } from "./server";

describe("the server address", () => {
  it("takes what a venue would type", () => {
    expect(parseServer("192.168.1.20:8000")).toEqual({ base: "http://192.168.1.20:8000" });
    expect(parseServer(" HTTPS://pos.example.com/ ")).toEqual({ base: "https://pos.example.com" });
    expect(parseServer("http://till-hub.local:8000/api/")).toEqual({ base: "http://till-hub.local:8000/api" });
    expect(parseServer("http://[fe80::1]:8000")).toEqual({ base: "http://[fe80::1]:8000" });
  });

  it("refuses what isn't an address", () => {
    expect(parseServer("")).toHaveProperty("error");
    expect(parseServer("not a server")).toHaveProperty("error");
    expect(parseServer("ftp://x")).toHaveProperty("error");
    expect(parseServer("http://x:99999")).toEqual({ error: "That port isn't valid." });
  });

  it("keeps the venue's choice across restarts", async () => {
    expect(serverBase()).toBe("http://localhost:8000");
    await saveServer("http://10.0.0.5:8000");
    expect(await AsyncStorage.getItem(SERVER_KEY)).toBe("http://10.0.0.5:8000");
    expect(await loadServer()).toBe("http://10.0.0.5:8000");
    expect(serverBase()).toBe("http://10.0.0.5:8000");
  });
});
