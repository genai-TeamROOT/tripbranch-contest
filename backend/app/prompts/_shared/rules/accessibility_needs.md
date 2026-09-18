accessibility_needs 판별 (무장애 요구):

발화에 무장애 요구가 있으면 아래 9개 값 중 해당하는 것만 accessibility_needs에 넣는다.
언급이 없으면 빈 배열로 둔다. 값을 많이 넣을수록 후보가 AND로 좁혀지므로, 발화에 없는
요구를 추가하지 않는다.

- wheelchair_access: "휠체어"로 들어갈 수 있는지를 물으면
- stroller_access: "유모차"로 들어갈 수 있는지를 물으면
  - 휠체어와 유모차는 다른 값이다. 같은 원문이라도 통로 폭·흙길 여부에 따라 판정이
    갈린다("휠체어"라고 말했으면 wheelchair_access, "유모차"면 stroller_access만 넣는다).
- accessible_restroom: 장애인 화장실
- accessible_parking: 장애인 주차구역
- visual_guide: 점자블록·점자안내·음성안내·안내견
- infant_facilities: 수유실·기저귀교환대
  - "유모차 끌고 갈 만한 곳"처럼 유모차 접근과 유아 동반을 함께 말하면
    stroller_access + infant_facilities 두 값을 모두 넣는다. infant_facilities만
    넣으면 수유실은 있지만 계단으로 올라가야 하는 곳이 섞인다.
- wheelchair_rental: 휠체어 **대여**("휠체어 빌릴 수 있는 곳" 등). 휠체어로 들어갈 수
  있는지(wheelchair_access)와는 다른 질문이다.
- seating_available: 의자식(입식) 좌석("좌식 말고 의자 있는 곳")
- low_floor_transit: 저상버스·역 엘리베이터로 접근하기 쉬운지

노인 동반처럼 오래 걷기 힘든 경우("어머니가 오래 못 걸으셔", "할머니 모시고")는 휠체어를
쓰지 않는 한 wheelchair_access를 넣지 않는다 — seating_available·wheelchair_rental·
low_floor_transit 중 발화가 가리키는 값만 넣는다. wheelchair_access를 붙이면 휠체어
접근 조건까지 걸려 후보만 불필요하게 좁아진다.
