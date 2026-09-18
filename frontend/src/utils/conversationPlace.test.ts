import { describe, expect, test } from "vitest";
import type { ChatMessage } from "../types";
import { getLatestConversationPlaceName } from "./conversationPlace";

const infoCardMessage = (placeName: string | null): ChatMessage => ({
  id: "info-card",
  type: "place_info_result",
  card: {
    question_type: "parking",
    answer_fields: {},
    place_id: "place-1",
    place_name: placeName,
    latitude: null,
    longitude: null,
    thumbnail_url: null,
    overview: null,
    operating_hours: null,
    rest_date: null,
    parking: null,
    parking_fee: null,
    fee: null,
    baby_carriage: null,
    pet: null,
    credit_card: null,
    restroom: null,
    homepage: null,
  },
});

describe("getLatestConversationPlaceName", () => {
  test("uses the most recent INFO card place name", () => {
    const messages: ChatMessage[] = [
      infoCardMessage("경복궁"),
      { id: "user", type: "user_text", text: "건청궁 주차 돼?" },
      infoCardMessage("건청궁"),
    ];

    expect(getLatestConversationPlaceName(messages)).toBe("건청궁");
  });

  test("returns null without an INFO card place name", () => {
    expect(getLatestConversationPlaceName([infoCardMessage(null)])).toBeNull();
  });
});
