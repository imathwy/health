# Tracking metrics and evidence rules

Schema v3 adds a small set of observations that can change a decision. It does
not turn missing values into zero and does not estimate measurements such as
body weight from photos.

## Core definitions

| Metric | Canonical meaning | Reference used by the public example | Evidence rule |
|---|---|---|---|
| Direct drinking water | Water, unsweetened sparkling water, or unsweetened light tea drunk directly; excludes water in food, soup, milk, juice, and other caloric drinks | 1,700 mL/day base for an adult man in a mild climate with low activity; increase for heat and exercise | User report, measured bottle volume, or a defensible range; a photographed bottle alone does not prove the whole volume was consumed |
| Calcium | Total calcium from food, fortified products, and supplements | 1,000 mg/day for adults age 19–50 | Sum only label/database-supported `calcium_mg`; incomplete food coverage remains `partial` |
| Heme-iron meal frequency | Number of reviewed meals containing a meaningful meat, poultry, fish, or seafood source | Observation only; there is no independent RDA for “heme-iron meals per week” | Add meal tag `heme_iron`; eggs and dairy do not qualify; incomplete meal coverage makes the count a confirmed minimum |
| Iron–calcium timing | Whether a separate iron supplement overlaps with a calcium supplement | Do not rearrange ordinary mixed meals solely for this field; take separate calcium and iron supplements at different times when both are used; follow the iron label or clinician for high-calcium foods | Record one categorical status and the user-reported timing; never infer an interaction from food photos alone |
| Per-meal protein | Sum of all item protein ranges in one meal | Public default 20–40 g; a local profile may choose a narrower range | Derived automatically from meal items; it is not duplicated as manual data |
| Weight and circumferences | Scale or tape measurements taken by the user | Trend metric, not a daily target | Prefer the same time, device, posture, tape location, and pre/post-meal context; one value is a baseline, not a trend |

The direct-water base follows the Chinese National Health Commission's adult
male guidance. Calcium and supplement-timing references come from NIH Office of
Dietary Supplements. The protein range follows the ISSN position stand.

## Additional high-value observations

The schema also includes sleep duration, caffeine amount and last-use time,
training duration, session RPE, vegetables, and fruit. These were chosen because
they can explain recovery, training progress, energy intake, and diet quality
without requiring a new device. Existing `day_context.training_notes` remains
the place for lift, tennis, swim, or performance details.

Home body-fat percentage, HRV, continuous glucose, and resting heart rate are
not core fields yet. They either have high device noise or need an Apple Health
ingestion boundary before they can be interpreted consistently.

## Missingness and provenance

Every numeric observation has:

- `range`: `[low, high]`, or `null` when unknown;
- `source`: `user_reported`, `measured`, `photo_review`,
  `derived_from_items`, `package_label`, `wearable`, or `unknown`;
- `coverage`: `complete`, `partial`, or `unknown`;
- `notes`: short assumptions or measurement context.

A complete zero, such as no caffeine on a fully logged day, is `[0, 0]` with
`coverage: complete`. Unknown remains `null`. Reports average only known days
and show how many of those days were complete.

## Public references

- [China National Health Commission: Health literacy interpretation (2024)](https://www.nhc.gov.cn/xcs/c100123/202404/c30d31cb877749ce94ce23d2c55ad973/files/1733220375762_77083.pdf)
- [NIH ODS calcium fact sheet](https://ods.od.nih.gov/factsheets/Calcium-HealthProfessional/)
- [NIH ODS iron fact sheet](https://ods.od.nih.gov/factsheets/Iron-HealthProfessional/)
- [ISSN position stand: protein and exercise](https://pmc.ncbi.nlm.nih.gov/articles/PMC5477153/)
- [CDC adult sleep duration](https://www.cdc.gov/sleep/about/index.html)
- [FDA caffeine guidance](https://www.fda.gov/consumers/consumer-updates/spilling-beans-how-much-caffeine-too-much)
- [American Heart Association fish guidance](https://www.heart.org/en/healthy-living/healthy-eating/eat-smart/fats/fish-and-omega-3-fatty-acids)
