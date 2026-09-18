import { beforeEach, expect, test, vi } from "vitest";
import { getBrowserDeviceLocation } from "./geolocation";

beforeEach(() => {
  vi.unstubAllEnvs();
});

test("uses configured local test coordinates without requesting browser permission", async () => {
  vi.stubEnv("VITE_TEST_DEVICE_LOCATION", "37.5796,126.9769");
  const getCurrentPosition = vi.fn();
  vi.stubGlobal("navigator", { geolocation: { getCurrentPosition } });

  await expect(getBrowserDeviceLocation()).resolves.toBe("37.5796,126.9769");
  expect(getCurrentPosition).not.toHaveBeenCalled();
});

/*
 * 전에는 타임아웃 문구가 macOS 설정 경로를 그대로 안내했다(TP-250). 안드로이드·
 * 윈도우·iOS 사용자에게는 존재하지 않는 경로라, 그대로 두면 틀린 안내가 된다.
 */
test("위치 조회가 시간 초과되면 특정 기기의 설정 경로를 안내하지 않는다", async () => {
  vi.stubEnv("VITE_TEST_DEVICE_LOCATION", "");
  vi.stubGlobal("navigator", {
    geolocation: {
      getCurrentPosition: vi.fn((_success: PositionCallback, error: PositionErrorCallback) =>
        error({
          code: 3,
          PERMISSION_DENIED: 1,
          POSITION_UNAVAILABLE: 2,
          TIMEOUT: 3,
          message: "timeout",
        } as GeolocationPositionError),
      ),
    },
  });

  await expect(getBrowserDeviceLocation()).rejects.toThrow(/위치 권한을 확인해주세요/);
  await expect(getBrowserDeviceLocation()).rejects.not.toThrow(/macOS/);
});
