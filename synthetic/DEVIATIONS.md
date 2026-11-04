# Synthetic seat: deviations from docs/W1_SPINE.md §1

Scope: the files under `synthetic/out/<run_id>/`. Each item says what the spine says, what real
X12 005010 (or a state feed) requires, and what the generator emits. All changes are **additive**.
Every segment and element the spine names is still present, in the order the spine gives, with the
spine's values. A parser written against the spine needs only the stated caveats (items D4 and D5).

## Verified correct as written (no change)

- **ISA layout.** The template is exactly 106 characters including the `~` terminator. ISA06/ISA08 are padded to 15, and ISA11=`^`, ISA12=`00501`, ISA14=`0`, ISA15=`P`, ISA16=`:`. Every regular file is checked at 106.
- **SE01.** It counts ST through SE inclusive.
- **GE01 and IEA01.** GE01 is the ST count and IEA01 is the GS count.
- **Control-number echoes.** ST02=SE02, GS06=GE02, ISA13=IEA02 and GS08=ST03.
- **834 element positions.**
  - `BGN*00*ref*date*time****<BGN08>`: BGN08 is the 8th element.
  - `INS*Y*18*<03>*<04>*A***AC`: INS08 sits in element 8.
  - `HD*<maint>**HMO*<plan>` and `DTP*348/349` match the IG.
- **837P element positions.**
  - `NM1*85*2*org*****XX*npi`
  - `SBR*P*18*******MC` (SBR09=MC)
  - `NM1*IL*1*last*first****MI*id`
  - `CLM*id*amt***POS:B:freq*Y*A*Y*Y`
  - `HI*ABK:`
  - `SV1*HC:proc*charge*UN*units***1` (SV107 pointer)
  - `DTP*472*D8` at line level
  - `BHT06=RP` for encounter reporting
- **REF*F8.** On frequency 7/8 it sits in loop 2300 between CLM and HI, as the IG orders it.

## Deviations

**D1. 837P loops 1000A and 1000B added (required by 005010X222A1).**
- Every ST carries `NM1*41*2*<MCO name>*****46*<ZZMCOA|ZZMCOB>`, `PER*IC*EDI SUPPORT*TE*5555550142` and `NM1*40*2*ZZ MEDICAID*****46*ZZMEDICAID`.
- These sit between BHT and the first HL. They belong to no HL.

**D2. 837P loop 2010AA completed (required).**
- `N3`, `N4` and `REF*EI*<EIN>` follow the billing-provider `NM1*85`.
- Atypical (non-NPI) billing providers carry `NM1*85*2*<name>` with NM108/NM109 omitted. Their payer-assigned ID goes in `REF*G2` in loop 2010BB, which is the 5010 pattern for atypical providers.
- A missing NPI is therefore legitimate and is not an NPI_LUHN defect.

**D3. 837P subscriber loops completed (required when SBR02=18).**
- 2010BA gains `N3`, `N4` and `DMG*D8*dob*sex` after the spine's `NM1*IL`.
- 2010BB payer `NM1*PR*2*ZZ MEDICAID*****PI*ZZMEDICAID` is added.

**D4. 837P `AMT*D` moved into loop 2320, with 2330A/2330B and 2430 added.**
- The spine places `AMT*D` directly after HI and labels it a simplification. In X222A1, loop 2300 only allows `AMT*F5` (patient amount paid), positioned before REF. The payer-paid amount (`AMT*D`) belongs to loop 2320 (Other Subscriber Information).
- That is where encounter reporting carries the MCO's adjudication. Each claim now has:
  `SBR*S*18*******HM` / `AMT*D*<paid>` / `OI***Y*P**Y` / `NM1*IL...MI*<member>` (2330A) /
  `NM1*PR*2*<MCO name>*****PI*<ZZMCOA|ZZMCOB>` (2330B).
- Each line adds `SVD*<MCO id>*<line paid>*HC:<proc>**<units>`, `CAS*CO*45*<charge-paid>` when charge > paid, and `DTP*573*D8*<adjudication date>` after the spine's `DTP*472`.
- **Parser caveats:**
  - `AMT*D` still appears exactly once per claim, between HI and the first LX.
  - The 2320 `SBR` and the 2330A `NM1*IL` are inside the claim. They are not preceded by an HL and must not be read as a new subscriber.
  - The 2330A member ID always equals the 2010BA member ID.
  - Line service date is `DTP*472`. `DTP*573` is adjudication and must not be read as service date. Select DTP by qualifier.
  - Line paid amounts sum to `AMT*D` on every claim.
- **SBR01 choice.** The spine's 2000B `SBR*P` is kept, so the MCO in 2320 is `S`, because SBR01 must differ between 2000B and 2320. Many state encounter companion guides do the reverse (MCO=`P`, state=`S`). That choice belongs to the companion guide and has no effect on any W1 check.

**D5. 834 loops 1000A and 1000B added (required by 005010X220A1).**
- `N1*P5*ZZ MEDICAID AGENCY*FI*009900001` and `N1*IN*ZZ MEDICAID MANAGED CARE*FI*009900002` follow BGN, before the first INS.
- Member loops begin at INS, as in the spine.

**D6. 834 maintenance values made concrete.**
- **Full file (BGN08=4, Verify).** Every member is `INS03=030` / `HD01=030` (audit or compare), with `INS04=AI`.
- **Change file (BGN08=2).** It uses the following codes:
  - `021` add, with INS04 `28` (initial enrollment) or `41` (re-enrollment)
  - `024` term, with INS04 `07` and `DTP*349`
  - `025` reinstatement, with INS04 `41`
  - `001` change, with INS04 `AI`, used for plan changes within the same MCO, where `DTP*348` is the plan-change effective date
- HD01 always equals INS03.

**D7. Filename and partner tokens.**
- `<MCO>` in `837P_<MCO>_...` and `ack_<MCO>_...` is the ISA06/GS02 ID (`ZZMCOA`, `ZZMCOB`), by analogy with `834_ZZBROKER_`. The registry partner names stay `ZZ-MCO-A` / `ZZ-MCO-B` / `ZZ-BROKER`.
- `<n>` is the arrival sequence for that sender across the run, not within one date.

**D8. Acks also emitted for the broker 834.**
- The spine says partner = the MCO. The translator also acks the 834, so `ack_ZZBROKER_<date>_<n>.csv` is written in the same format.
- `error_code` is `IK502:<code>` for transaction-set errors and `AK905:<code>` for group errors. It is empty when the ST is clean.

**D9. Translator model (world assumption, needed to make the ack CSV meaningful).**
The ZZ translator behaves as follows:
- It rejects an ST whose SE01 is wrong: `R`, `IK502:4`.
- It reports a GE01 mismatch as a group error while accepting the STs (999 `AK901=E`, `AK905=5`): status `A`, error_code `AK905:5`.
- It tokenises by delimiter, so it does **not** enforce the 106-character ISA width.
- It has **no** duplicate-interchange check.
- It can reject an otherwise W1-clean ST at implementation level: `R`, `IK502:5` or `IK502:I5`.

ISA width and duplicate interchanges are therefore visible only to the agent's own envelope checks, which matches DESIGN §13: the agent sits behind the translator. TA1 is not emitted.

**D10. IE extract delivered as a weekly series, not a single file.**
- The first file (`file_seq` 1, run start date) is a full snapshot of segments recorded before the run start.
- Each later `ie_extract_<YYYYMMDD>.txt` holds the segment versions recorded since the previous file. These include retro-dated segments and same-key corrections that carry a later `recorded_at`.
- The last file is dated at run end. Some weekly files arrive 1 to 3 days late.
- The current eligibility state is the latest `recorded_at` per (person_source_key, program, segment_type, eff_start), consistent with the spine's silver key.
- Ledger `line_no` is the 1-based physical line in the file (HDR = line 1).

**D11. SNAP rows.**
- SNAP-only persons have an **empty** `msis_id` (no MSIS ID outside Medicaid) and `elig_group=SN`. SNAP `segment_type` is always `FULL`.
- About 10% of IE rows are SNAP-only. Some Medicaid households also carry SNAP rows that use their Medicaid msis_id.

**D12. Synthetic code and identifier spaces.**
- **Procedure codes.**
  - `Z9001`–`Z9060` are CPT-shaped and sent as `SV101` qualifier `HC`. There are no national HCPCS "Z" codes, so the space cannot collide with real codes.
  - The public HCPCS Level II codes used are `G0008`, `G0444`, `A4253` and `Q3014`, plus `T2003`, `T1019` and `S5170` for the atypical providers.
- **Diagnosis codes.** These are public ICD-10-CM codes written without the decimal point.
- **Eligibility groups.** `elig_group` values follow T-MSIS ELIGIBILITY-GROUP shapes: 01, 05, 06, 07, 11, 23, 24 and 72.
- **Addresses and tax IDs.**
  - The N4 state code is `ZZ`, which is not a USPS code.
  - ZIP codes fall in the unassigned `000xx` range.
  - EINs carry the never-issued `00` prefix.
  - Phone numbers fall in the 555-01xx fictional range.
- **Member IDs.** `msis_id` is `ZZ` + 8 digits.

**D13. Amount formatting.** Amounts keep the spine's 2-decimal form (`120.00`). X12 allows this, although many production translators suppress trailing zeros.

**D14. Segment terminator only, no line breaks.** Each `.x12` file is a single line, with `~` immediately followed by the next segment tag.

**D15. PART C seeded classes (opt-in, additive).**
- **Opt-in.** `--class-set w1+partc` (API: `class_set="w1+partc"`). The default `w1` set is unchanged, so a default run, and every W1 commitment already in `LEDGER_COMMITMENTS.log`, regenerates byte-identically. The PART C picks use their own RNG stream (`<seed>:partc`) and run after W1 encounter seeding, so every W1 seed lands at the same location in a PART C run. The one visible effect is that `SE_COUNT_MISMATCH` `se01_reported`/`segments_actual` can shift when the same ST also loses DTP*573 segments. The delta is unchanged.
- **Commitment line.** A non-default class set appends `\tclass_set=<set>` to the commitment line. Default lines are unchanged.
- **Classes and realism decisions:**
  - `INVALID_POS`: CLM05-1 is mangled by stripping its leading zero (`02`→`2`), by truncation (`11`→`1`), or by keying the letter O for a zero (`O2`). Every value stays within X12 AN 1..2, so the syntax-only translator (D9) accepts the ST. `A1` was dropped as implausible. An unassigned 2-digit code would be a code-set defect, not a shape defect.
  - `INVALID_DX_SHAPE`: HI ABK is seeded with a decimal point (`E11.9`), an ICD-9-CM code leaked under the ICD-10 qualifier (`4019`), or lowercase. **"Starts with U" was dropped** because U07.1 and U09.9 are valid ICD-10-CM codes, so a U-prefix seed would score a correct rule as a miss.
  - `INVALID_FREQ`: CLM05-3 is set to `2`, `5`, `6` or `0`. These are UB-04 institutional frequency codes that bleed into 837P, which allows only 1, 7 and 8.
  - `MISSING_FREQ`: CLM05 is emitted as `POS:B`, with the trailing `:` suppressed per X12. The mapper does **not** reject it: the header lands MAPPED with `freq_code` NULL, and that is the ledger expectation. A SNIP-2 translator would reject it, but the ZZ translator is syntax-only (D9), consistent with ISA_WIDTH.
  - `MISSING_ADJUD_DATE`: no 2430 `DTP*573` on any line of the encounter. SVD and CAS are kept.
  - `LATE_SUBMISSION`: every line's DTP*472 is moved back by one offset so that DTP*573 − DTP*472 > 365 days on every line (366–372, 400–500 or 700–800 days). DTP*573 stays before the file date. Every moved date is inside a conveyed FULL span, so there is no SVC_OUTSIDE_ELIG side effect. A negative control, `ADJUD_LAG_EXACTLY_365`, covers the boundary. **Citation caveat:** 42 CFR 447.45(d)(1) is the FFS 12-month filing limit on claims to the agency. MCO provider filing limits are contractual, and 447.45(d)(4) allows exceptions. The ledger therefore labels this a review flag, not a hard rejection.
  - `ELIG_BAD_SEX`: the IE `sex` value is a numeric code (`1`/`2`), the long form (`MALE`/`FEMALE`) or lowercase, on every DTL row of the person. The 834/837 DMG values stay clean because the broker maps them. `X` was not seeded, because some states now accept it.
- **Ledger.** Encounter classes use the W1 X12 locator (`isa13`/`gs06`/`st02`/`encounter_id` plus the observed value and the variant). `ELIG_BAD_SEX` follows the `DOB_AFTER_ELIG_START` row-list convention. Each encounter entry carries `st_ack`. When the ST is translator-rejected (`R`), `landing` says the encounter is filtered before bronze.
- **Oracle.** `tests/synthetic/oracle.py` now reads CLM05-3 tolerantly when the component is absent. W1 findings are unchanged.

## Known remaining simplifications (not corrected; out of W1 scope)

- **837P:**
  - No 2010AA `PRV` taxonomy and no 2310B rendering provider.
  - No 2300 `DTP` claim-level dates.
  - At most 2 diagnosis codes per claim.
- **834:**
  - No member `DTP*303` maintenance effective date and no `N3`/`N4` residence.
  - `DTP*348` equals the IE `eff_start`. MCO enrollment is modelled as identical to the FULL span.
- **Acknowledgements.** The ack CSV is a per-ST summary. No 999, TA1 or 277CA files are produced.
