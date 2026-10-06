import { describe, expect, it } from "vitest";
import type { OpportunityCardData, RevisionCard } from "./crmTypes";
import { FOLLOW_UP_AFTER_DAYS, STALE_AFTER_DAYS, groupFollowUps } from "./followUps";

// Every value below is invented; the repository is public.
const NOW = new Date("2026-03-31T15:00:00Z");

function revision(sentAt: string | null, quoteNumber: string): RevisionCard {
  return {
    revision_id: `r-${quoteNumber}`,
    revision_no: 1,
    status: "sent",
    origin: "historical_import",
    sent_at: sentAt,
    superseded_by_revision_no: null,
    is_active: true,
    document: null,
    gmail: null,
    drive: null,
    quote_number: quoteNumber,
  };
}

function card(id: string, sentAt: string | null, over: Partial<OpportunityCardData> = {}): OpportunityCardData {
  const latest = sentAt === null ? null : revision(sentAt, `0${id}-26`);
  return {
    opportunity_id: id,
    title: `Caso ${id}`,
    stage: "quoting",
    created_at: null,
    updated_at: null,
    closed_at: null,
    close_reason: null,
    organization: { organization_id: `org-${id}`, name: `Institución ${id}`, confirmation: "confirmed" },
    other_organizations: [],
    contact: null,
    quotes: [],
    quote_numbers: latest ? [latest.quote_number] : [],
    revision_count: latest ? 1 : 0,
    latest_revision: latest,
    drive_folder: null,
    attention: [],
    status: "ok",
    next_action: { text: "", source: "suggested", due_at: null },
    ...over,
  };
}

describe("groupFollowUps", () => {
  it("splits open quotes by days since sent: this week, to follow up, older than a month", () => {
    const out = groupFollowUps(
      [
        card("1001", "2026-03-30T12:00:00Z"), // 1 day
        card("1002", "2026-03-24T15:00:00Z"), // 7 days: still this week
        card("1003", "2026-03-23T15:00:00Z"), // 8 days: follow up
        card("1004", "2026-03-01T15:00:00Z"), // 30 days: follow up
        card("1005", "2026-02-28T15:00:00Z"), // 31 days: older
      ],
      NOW,
    );
    expect(FOLLOW_UP_AFTER_DAYS).toBe(7);
    expect(STALE_AFTER_DAYS).toBe(30);
    expect(out.thisWeek.map((i) => i.card.opportunity_id)).toEqual(["1002", "1001"]);
    expect(out.followUp.map((i) => i.card.opportunity_id)).toEqual(["1004", "1003"]);
    expect(out.older.map((i) => [i.card.opportunity_id, i.days])).toEqual([["1005", 31]]);
  });

  it("puts the longest-waiting first within a group", () => {
    const out = groupFollowUps(
      [card("1", "2026-03-20T10:00:00Z"), card("2", "2026-03-10T10:00:00Z"), card("3", "2026-03-15T10:00:00Z")],
      NOW,
    );
    expect(out.followUp.map((i) => i.card.opportunity_id)).toEqual(["2", "3", "1"]);
  });

  it("leaves out closed cases and lists open cases with no sent quote apart", () => {
    const out = groupFollowUps(
      [
        card("won", "2026-03-20T10:00:00Z", { stage: "won", closed_at: "2026-03-25T10:00:00Z" }),
        card("lost", "2026-03-20T10:00:00Z", { stage: "lost", closed_at: "2026-03-25T10:00:00Z" }),
        card("lead", null, { stage: "lead" }),
        card("open", "2026-03-20T10:00:00Z"),
      ],
      NOW,
    );
    expect(out.followUp.map((i) => i.card.opportunity_id)).toEqual(["open"]);
    expect(out.withoutQuote.map((c) => c.opportunity_id)).toEqual(["lead"]);
    expect(out.openWithQuote).toBe(1);
  });

  it("reports the newest send date it knows, so the page can say how fresh the data is", () => {
    const out = groupFollowUps(
      [card("1", "2026-03-20T10:00:00Z"), card("2", "2026-03-25T22:00:00Z"), card("3", null)],
      NOW,
    );
    expect(out.latestSentAt).toBe("2026-03-25T22:00:00Z");
    expect(groupFollowUps([], NOW).latestSentAt).toBeNull();
  });

  it("never counts a send date in the future as negative days", () => {
    const out = groupFollowUps([card("1", "2026-04-02T10:00:00Z")], NOW);
    expect(out.thisWeek[0].days).toBe(0);
  });
});

describe("último contacto", () => {
  const touch = (at: string) => ({ at, subject: "Re: cotización", url: `https://mail.example.invalid/${at}` });

  it("ages a case from a follow-up email sent after the quote", () => {
    const followedUp = card("2001", "2026-02-20T15:00:00Z", {
      last_contact: { outbound: touch("2026-03-27T15:00:00Z"), inbound: null },
    });
    const out = groupFollowUps([followedUp], NOW);
    expect(out.older).toEqual([]);
    expect(out.thisWeek.map((i) => [i.card.opportunity_id, i.days, i.lastTouch, i.lastTouchAt, i.replied])).toEqual([
      ["2001", 4, "email", "2026-03-27T15:00:00Z", false],
    ]);
  });

  it("ignores an email older than the quote", () => {
    const c = card("2002", "2026-03-20T15:00:00Z", { last_contact: { outbound: touch("2026-03-01T15:00:00Z"), inbound: null } });
    const [item] = groupFollowUps([c], NOW).followUp;
    expect([item.lastTouch, item.days]).toEqual(["quote", 11]);
  });

  it("marks a client who wrote after the last touch, and puts them first", () => {
    const waiting = card("2003", "2026-03-10T15:00:00Z");
    const answered = card("2004", "2026-03-20T15:00:00Z", {
      last_contact: { outbound: null, inbound: touch("2026-03-25T15:00:00Z") },
    });
    const answeredBefore = card("2005", "2026-03-15T15:00:00Z", {
      last_contact: { outbound: touch("2026-03-22T15:00:00Z"), inbound: touch("2026-03-18T15:00:00Z") },
    });
    const out = groupFollowUps([waiting, answeredBefore, answered], NOW);
    expect(out.followUp.map((i) => [i.card.opportunity_id, i.replied])).toEqual([
      ["2004", true],
      ["2003", false],
      ["2005", false],
    ]);
  });
});
