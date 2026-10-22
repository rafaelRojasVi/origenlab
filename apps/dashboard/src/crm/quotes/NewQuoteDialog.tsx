/**
 * «Nueva cotización»: the operator picks a client; the API finds or opens its case and creates the
 * quote (`create-quote` with `client`), which then opens as a tab of the Cotizador module.
 *
 * - **Cliente**: a combobox over `GET /v2/quotes/clients` — asked 300 ms after the typing stops,
 *   and an answer is shown only while its text is still the one typed (an older one is aborted, and
 *   ignored if it lands anyway). Institutions first, then people (a person goes by `person_id`; an
 *   address with no person by `contact_point_id`), then an address the CRM lacks (`email`) or
 *   «+ Cliente nuevo «texto»» (`new_organization`, with its kind and an optional address).
 * - **Caso**: the client's open cases and «Abrir una Solicitud nueva». One open case is chosen by
 *   itself; with none, or for a new client, a new Solicitud is the only choice. For a client whose
 *   cases the dialog does not know (a typed address) the API decides, and asks back
 *   (`case_choice_needed`) when there are several: they are then listed here to choose from.
 * - **Crear y abrir**: one `Idempotency-Key` per command. A refusal for certain (`definitive`, as
 *   «Cotizar» decides it) drops it, except the two that ask the operator to choose
 *   (`case_choice_needed`, `organization_name_ambiguous`): a refusal leaves no receipt, so the
 *   retry with the choice keeps it. A network failure, a gateway error or a body that is not the
 *   API's may have run: the next click with the same request repeats the same command.
 * - A «+ Cliente nuevo» the API resolved to an institution it already had is said once the tab
 *   opens: «Se usó la institución existente «X»» (and «y su caso abierto» when it used that case).
 * - **Institución** (a person, or an address, the CRM knows no institution of): optional — an
 *   institution held (searched with the same suggestions) or «+ Nueva institución «texto»» with its
 *   kind, sent as `client.institution`. The API connects the person to it (an affiliation) and
 *   names it on the case; without one the quote cannot be approved, and the field says so. When
 *   its name is shared by several, the institutions of that name are listed to pick from, as for
 *   a new client.
 *
 * Refusals are said in Spanish (`quoteRefusalText`). The browser never prices anything here.
 */
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { KIND_OPTIONS } from "../authoring/NewOrganizationForm";
import { daysSince } from "../caseDisplay";
import { Button, FormField, Modal, SelectInput, toast } from "../ui";
import { createRefusalText, definitive } from "./openOrCreateQuote";
import {
  QuoteRefusal,
  createQuote,
  fetchQuoteClients,
  newQuoteCommandKey,
  type CreateQuoteForClient,
  type CreatedQuote,
  type NewClientKind,
  type QuoteClientCase,
  type QuoteClientInstitution,
  type QuoteClientOrganization,
  type QuoteClientPerson,
  type QuoteClientPick,
  type QuoteClientSuggestions,
} from "./quoteApi";
import { quoteLabel } from "./quoteLabel";
import { quoteRefusalText } from "./quoteRefusal";
import type { TabMeta } from "./tabStore";

export const CLIENT_SEARCH_DELAY_MS = 300;
const MIN_SEARCH = 2;
const NAME_MAX = 500;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const isEmail = (text: string) => EMAIL.test(text);

/** The refusals that ask the operator to choose: the retry carries the choice, under the same key. */
const ASKS_TO_CHOOSE = new Set(["case_choice_needed", "organization_name_ambiguous"]);

type Client =
  | { kind: "organization"; org: QuoteClientOrganization; casesKnown: boolean }
  | { kind: "person"; person: QuoteClientPerson }
  | { kind: "email"; email: string }
  | { kind: "new"; name: string };

/** The institution a person or an address is connected to: one held, or a new one. */
type InstitutionPick = { kind: "existing"; id: string; name: string } | { kind: "new"; name: string };

/** A person or an address the CRM knows no institution of: the dialog offers to name one. */
function needsInstitution(c: Client | null): boolean {
  if (c?.kind === "email") return true;
  return c?.kind === "person" && !c.person.organization;
}

/** The case chosen: one of the client's, a new Solicitud, or the API's to find (`auto`). */
type CaseChoice = string | "new" | "auto" | null;

type Option = { key: string; client: Client; main: string; side: string | null; strong?: boolean };

function casesLine(n: number): string {
  if (n === 0) return "sin casos abiertos";
  return n === 1 ? "1 caso" : `${n} casos`;
}

function personSide(p: QuoteClientPerson): string {
  if (p.person_id && p.name) return `persona · ${p.name}`;
  if (p.person_id) return "persona";
  return "dirección sin persona";
}

/** The rows for `term` once its search answered; after a failed search, only the typed address or a new client. */
function optionsFor(term: string, found: Found | null): Option[] {
  if (!found) return [];
  const data: QuoteClientSuggestions = found.data ?? { organizations: [], people: [] };
  const out: Option[] = data.organizations.map((org) => ({
    key: `org:${org.id}`,
    client: { kind: "organization", org, casesKnown: true },
    main: org.name,
    side: `institución · ${casesLine(org.open_cases.length)}`,
  }));
  for (const p of data.people) {
    if (!p.person_id && !p.contact_point_id) continue;
    out.push({ key: `person:${p.person_id ?? p.contact_point_id}`, client: { kind: "person", person: p }, main: p.email ?? p.name ?? "Sin nombre", side: personSide(p) });
  }
  const typed = term.toLowerCase();
  if (isEmail(term)) {
    if (!data.people.some((p) => p.email?.toLowerCase() === typed)) {
      out.push({ key: "email", client: { kind: "email", email: typed }, main: `Usar la dirección ${typed}`, side: null, strong: true });
    }
  } else {
    out.push({ key: "new", client: { kind: "new", name: term.slice(0, NAME_MAX) }, main: `+ Cliente nuevo «${term}»`, side: null, strong: true });
  }
  return out;
}

/** What the input says once a client is chosen. */
function clientText(c: Client): string {
  if (c.kind === "organization") return c.org.name;
  if (c.kind === "person") return c.person.email ?? c.person.name ?? "";
  if (c.kind === "email") return c.email;
  return c.name;
}

/** Whom the new tab names: the institution, else the person or the address (as the quote's page does). */
function clientName(c: Client, institution: InstitutionPick | null = null): string {
  if (c.kind === "organization") return c.org.name;
  if (institution && needsInstitution(c)) return institution.name;
  if (c.kind === "person") return c.person.organization?.name ?? c.person.name ?? c.person.email ?? "Cliente";
  if (c.kind === "email") return c.email;
  return c.name;
}

/** The client's open cases, or null when the dialog does not know them (the API then finds them). */
function knownCases(c: Client): QuoteClientCase[] | null {
  if (c.kind === "organization") return c.casesKnown ? c.org.open_cases : null;
  if (c.kind === "person") return c.person.open_cases;
  if (c.kind === "new") return [];
  return null;
}

function defaultChoice(cases: QuoteClientCase[] | null): CaseChoice {
  if (cases === null) return "auto";
  if (cases.length === 0) return "new";
  return cases.length === 1 ? cases[0].id : null;
}

function requestedLine(c: QuoteClientCase, now: Date): string {
  const days = daysSince(c.requested_at, now);
  if (days === null) return "abierto";
  if (days === 0) return "abierto · pidió precio hoy";
  return `abierto · pidió precio hace ${days} ${days === 1 ? "día" : "días"}`;
}

type Found = { term: string; data: QuoteClientSuggestions | null; failed: boolean };

/** Suggestions for `term`, asked once the typing pauses; `null` until the answer for this very term. */
function useClientSuggestions(term: string, enabled: boolean): { found: Found | null; retry: () => void } {
  const [found, setFound] = useState<Found | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!enabled || term.length < MIN_SEARCH) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      fetchQuoteClients(term, controller.signal).then(
        (data) => {
          if (!controller.signal.aborted) setFound({ term, data, failed: false });
        },
        () => {
          if (!controller.signal.aborted) setFound({ term, data: null, failed: true });
        },
      );
    }, CLIENT_SEARCH_DELAY_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [term, enabled, tick]);
  return { found: found && found.term === term ? found : null, retry: () => setTick((t) => t + 1) };
}

/**
 * «+ Cliente nuevo» that the API resolved to an institution the CRM already had (same name, or its
 * address's domain): said once the tab is open, with its open case when that one was used without
 * the operator choosing it. Null for any other client, or a truly new institution.
 */
function reusedText(client: Client, made: CreatedQuote, chose: boolean, institution: InstitutionPick | null): string | null {
  if (institution?.kind === "new" && made.institution_connected && made.organization_created === false && made.organization_id) {
    return `Se usó la institución existente «${made.organization_name || institution.name}».`;
  }
  if (client.kind !== "new" || made.organization_created !== false || !made.organization_id) return null;
  const name = made.organization_name || client.name;
  return `Se usó la institución existente «${name}»${made.case_created === false && !chose ? " y su caso abierto" : ""}.`;
}

export function NewQuoteDialog({
  onClose,
  onCreated,
  now = () => new Date(),
}: {
  onClose: () => void;
  /** The quote was created: open it (the dialog is done). */
  onCreated: (quoteId: string, meta: TabMeta) => void;
  now?: () => Date;
}) {
  const ids = useId();
  const [text, setText] = useState("");
  const [client, setClient] = useState<Client | null>(null);
  const [active, setActive] = useState(0);
  const [kind, setKind] = useState<NewClientKind>("company");
  const [newEmail, setNewEmail] = useState("");
  const [choice, setChoice] = useState<CaseChoice>(null);
  /** The cases the API listed when it asked which one (`case_choice_needed`). */
  const [askedCases, setAskedCases] = useState<QuoteClientCase[] | null>(null);
  /** The institutions of one name the API listed (`organization_name_ambiguous`). */
  const [twins, setTwins] = useState<{ id: string; name: string }[] | null>(null);
  const [twinInfo, setTwinInfo] = useState<Map<string, QuoteClientOrganization>>(new Map());
  /** Whose name the twins share: the new client's, or the new institution's of a person or an address. */
  const [twinsOf, setTwinsOf] = useState<"client" | "institution">("client");
  const [institution, setInstitution] = useState<InstitutionPick | null>(null);
  const [institutionKind, setInstitutionKind] = useState<NewClientKind>("company");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef<{ key: string; body: string | null } | null>(null);
  /** Where the focus goes once the API asked to choose: the cases, or the institutions of that name. */
  const [focusTo, setFocusTo] = useState<"cases" | "twins" | null>(null);
  const casesRef = useRef<HTMLFieldSetElement>(null);
  const twinsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (focusTo === "cases") casesRef.current?.focus();
    else if (focusTo === "twins") twinsRef.current?.focus();
    if (focusTo) setFocusTo(null);
  }, [focusTo]);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const term = text.trim();
  const { found, retry } = useClientSuggestions(term, client === null);
  const options = useMemo(() => optionsFor(term, found), [term, found]);
  // Esc folds the suggestions first (the Modal's Esc then closes the dialog); typing unfolds them.
  const [folded, setFolded] = useState(false);
  const listOpen = client === null && term.length >= MIN_SEARCH && !folded;
  const listId = `${ids}-clientes`;

  function choose(next: Client, cases: QuoteClientCase[] | null = knownCases(next)) {
    setClient(next);
    setText(clientText(next));
    setChoice(defaultChoice(cases));
    setAskedCases(null);
    setTwins(null);
    setInstitution(null);
    setInstitutionKind("company");
    setNotice(null);
    setError(null);
    setActive(0);
  }

  function retype(value: string) {
    setText(value);
    setFolded(false);
    setClient(null);
    setChoice(null);
    setAskedCases(null);
    setTwins(null);
    setInstitution(null);
    setNotice(null);
    setError(null);
    setActive(0);
  }

  const emailProblem = client?.kind === "new" && newEmail.trim() !== "" && !isEmail(newEmail.trim());
  const cases = askedCases ?? (client ? knownCases(client) : null);

  function body(): CreateQuoteForClient | null {
    if (!client || choice === null) return null;
    let pick: QuoteClientPick;
    if (client.kind === "organization") pick = { organization_id: client.org.id };
    else if (client.kind === "person" || client.kind === "email") {
      const connected: QuoteClientInstitution | null =
        !institution || !needsInstitution(client)
          ? null
          : institution.kind === "existing"
            ? { organization_id: institution.id }
            : { new_organization: { name: institution.name, kind: institutionKind } };
      const extra = connected ? { institution: connected } : {};
      if (client.kind === "email") pick = { email: client.email, ...extra };
      else if (client.person.person_id) pick = { person_id: client.person.person_id, ...extra };
      else pick = { contact_point_id: client.person.contact_point_id!, ...extra };
    } else {
      const email = newEmail.trim().toLowerCase();
      pick = { new_organization: { name: client.name, kind, ...(email ? { email } : {}) } };
    }
    // A new client goes without `new_case` until the API has asked: it may find an institution it
    // holds (same name, the address's domain) and use its one open case; a truly new one gets a
    // Solicitud anyway. Once it listed several cases, «Abrir una Solicitud nueva» must say so, or
    // the same question comes back.
    if (choice === "new") return client.kind === "new" && askedCases === null ? { client: pick } : { client: pick, new_case: true };
    if (choice === "auto") return { client: pick };
    return { client: pick, opportunity_id: choice };
  }

  const request = body();
  const ready = !!request && !emailProblem && !busy && !(client?.kind === "new" && !client.name.trim());

  async function create() {
    if (!request || !ready || !client) return;
    const sent = JSON.stringify(request);
    const reuse = pending.current && (pending.current.body === null || pending.current.body === sent);
    const key = reuse ? pending.current!.key : newQuoteCommandKey();
    pending.current = { key, body: sent };
    setBusy(true);
    setError(null);
    try {
      const made = await createQuote(request, key);
      pending.current = null;
      if (!mounted.current) return;
      onCreated(made.quote_id, {
        label: quoteLabel({ label: null, quote_number: made.quote_number, revision_no: 1 }),
        client: clientName(client, institution),
        status: "draft",
      });
      focusTabWhenShown(made.quote_id);
      const reused = reusedText(client, made, askedCases !== null, institution);
      if (reused) toast(reused, "warn");
    } catch (e) {
      if (!mounted.current) return;
      const code = e instanceof QuoteRefusal ? e.code : null;
      if (code && ASKS_TO_CHOOSE.has(code)) {
        // No receipt was left: the retry, with the operator's choice, keeps the key.
        pending.current = { key, body: null };
        const raw = (e as QuoteRefusal).details?.candidates;
        const list = Array.isArray(raw) ? raw : [];
        setNotice(quoteRefusalText(e));
        if (code === "case_choice_needed") {
          setAskedCases(list as QuoteClientCase[]);
          setChoice(null);
          setFocusTo("cases");
        } else {
          const of = institution?.kind === "new" && needsInstitution(client) ? "institution" : "client";
          setTwinsOf(of);
          showTwins(list as { id: string; name: string }[], of === "institution" ? institution!.name : null);
          setFocusTo("twins");
        }
      } else {
        if (definitive(e)) pending.current = null;
        setError(createRefusalText(e));
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  /** The institutions of that name, told apart by what the suggestions know of them (domains, cases). */
  function showTwins(list: { id: string; name: string }[], named: string | null) {
    setTwins(list);
    setTwinInfo(new Map());
    const name = list[0]?.name ?? named ?? (client?.kind === "new" ? client.name : "");
    if (name.trim().length < MIN_SEARCH) return;
    fetchQuoteClients(name.trim()).then(
      (data) => {
        if (mounted.current) setTwinInfo(new Map(data.organizations.map((o) => [o.id, o])));
      },
      () => undefined,
    );
  }

  function pickTwin(t: { id: string; name: string }) {
    if (twinsOf === "institution") {
      // The client stays; the institution it is connected to is the one chosen. The key stays too.
      setInstitution({ kind: "existing", id: t.id, name: t.name });
      setTwins(null);
      setNotice(null);
      return;
    }
    const info = twinInfo.get(t.id);
    const org: QuoteClientOrganization = info ?? { id: t.id, name: t.name, kind: null, domains: [], open_cases: [] };
    // The key stays (`pending`): the retry carries the institution chosen.
    choose({ kind: "organization", org, casesKnown: !!info });
  }

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape" && listOpen) {
      // Only the suggestions close; the dialog stays (the Modal listens on the document).
      e.stopPropagation();
      e.nativeEvent.stopImmediatePropagation();
      setFolded(true);
      return;
    }
    if (!listOpen || !options.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, options.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const o = options[Math.min(active, options.length - 1)];
      if (o) choose(o.client);
    }
  };

  return (
    <Modal title="Nueva cotización" onClose={onClose} busy={busy}>
      <div className="space-y-4">
        <div className="space-y-1.5">
          <label htmlFor={`${ids}-cliente`} className="block text-[13px] text-ink-muted">
            Cliente
          </label>
          <input
            id={`${ids}-cliente`}
            type="text"
            role="combobox"
            autoComplete="off"
            aria-expanded={listOpen}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={listOpen && options[active] ? `${listId}-${active}` : undefined}
            value={text}
            disabled={busy}
            placeholder="Institución, persona o correo"
            onChange={(e) => retype(e.target.value)}
            onKeyDown={onKey}
            className="h-11 w-full min-w-0 rounded-[10px] border-[1.5px] border-line-strong bg-canvas-raised px-3 text-[14.5px] text-ink placeholder:text-ink-faint focus:border-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-600/30 disabled:opacity-60"
          />
          {listOpen ? (
            <SuggestionList
              id={listId}
              options={options}
              active={active}
              loading={!found}
              failed={!!found?.failed}
              onRetry={retry}
              onHover={setActive}
              onPick={(o) => choose(o.client)}
            />
          ) : null}
        </div>

        {/* Said when the API asks the operator to choose (one case, or one of the institutions of a name). */}
        <p role="status" className={notice ? "text-[13px] text-ink" : "sr-only"}>
          {notice ?? ""}
        </p>

        {twins ? (
          <div ref={twinsRef} tabIndex={-1} role="group" aria-label="¿Cuál de ellas?" className="space-y-1.5 focus:outline-none">
            <div className="overflow-hidden rounded-[10px] border border-line">
              {twins.map((t, i) => {
                const info = twinInfo.get(t.id);
                const side = info ? [info.domains.join(", "), `institución · ${casesLine(info.open_cases.length)}`].filter(Boolean).join(" · ") : "institución";
                return (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => pickTwin(t)}
                    className={`flex min-h-[44px] w-full items-center justify-between gap-3 px-3 py-2 text-left text-[14px] text-ink hover:bg-brand-50 ${i ? "border-t border-line" : ""}`}
                  >
                    <span className="font-semibold">{t.name}</span>
                    <span className="text-[12.5px] text-ink-muted">{side}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ) : null}

        {client?.kind === "new" && !twins ? (
          <div className="grid grid-cols-2 gap-3">
            <FormField label="Tipo de institución" htmlFor={`${ids}-tipo`}>
              <SelectInput id={`${ids}-tipo`} value={kind} onChange={(v) => setKind(v as NewClientKind)} options={KIND_OPTIONS} disabled={busy} />
            </FormField>
            <FormField
              label="Correo (opcional)"
              htmlFor={`${ids}-correo`}
              error={emailProblem ? "Escribe una dirección de correo válida, o déjala vacía." : null}
            >
              <input
                id={`${ids}-correo`}
                type="email"
                value={newEmail}
                disabled={busy}
                maxLength={320}
                onChange={(e) => setNewEmail(e.target.value)}
                className="w-full min-w-0 rounded-md border border-line bg-canvas-raised px-2.5 py-1.5 text-xs text-ink focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600"
              />
            </FormField>
          </div>
        ) : null}

        {client && !twins && needsInstitution(client) ? (
          <InstitutionField
            picked={institution}
            kind={institutionKind}
            personName={client.kind === "person" ? (client.person.name ?? null) : null}
            busy={busy}
            onPick={(next) => {
              setInstitution(next);
              setError(null);
            }}
            onKind={setInstitutionKind}
          />
        ) : null}

        {client && !twins ? (
          <>
            <fieldset ref={casesRef} tabIndex={-1} className="m-0 flex flex-col gap-2 border-0 p-0 focus:outline-none" disabled={busy}>
              <legend className="mb-1.5 text-[13px] text-ink-muted">Caso</legend>
              {cases === null ? (
                <CaseRadio name={`${ids}-caso`} checked={choice === "auto"} onChoose={() => setChoice("auto")}>
                  <span className="font-semibold">Su caso abierto, si tiene uno</span>
                  <span className="block text-[12.5px] text-ink-muted">si tiene varios, se te pregunta cuál</span>
                </CaseRadio>
              ) : (
                cases.map((c) => (
                  <CaseRadio key={c.id} name={`${ids}-caso`} checked={choice === c.id} onChoose={() => setChoice(c.id)}>
                    <span className="font-semibold">{c.title || "Caso sin título"}</span>
                    <span className="block text-[12.5px] text-ink-muted">{requestedLine(c, now())}</span>
                  </CaseRadio>
                ))
              )}
              <CaseRadio name={`${ids}-caso`} checked={choice === "new"} onChoose={() => setChoice("new")}>
                <span>Abrir una Solicitud nueva</span>
                <span className="block text-[12.5px] text-ink-muted">el sistema la crea al guardar</span>
              </CaseRadio>
            </fieldset>
            <p className="text-[12.5px] text-ink-muted">
              Si el cliente tiene un solo caso abierto, se elige solo. Si no tiene ninguno, se abre una Solicitud nueva.
            </p>
          </>
        ) : null}

        {error ? (
          <p role="alert" className="text-[13px] text-bad">
            {error}
          </p>
        ) : null}

        <div className="flex justify-end gap-2">
          <Button onClick={onClose} disabled={busy} className="min-h-[44px] px-4 text-[14px] font-semibold">
            Cancelar
          </Button>
          <Button
            variant="primary"
            onClick={() => void create()}
            disabled={!ready}
            busy={busy}
            busyLabel="Creando…"
            className="min-h-[44px] px-[18px] text-[14px] font-semibold"
          >
            Crear y abrir
          </Button>
        </div>
      </div>
    </Modal>
  );
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Once the dialog is gone, the Modal gives the focus back to the button that opened it — which the
 * new tab may have just hidden («Nueva cotización» in «Todas»). The focus goes to the quote's tab
 * instead, once it is shown (the module's `[role=tab][data-quote-id]`), unless the operator has put
 * it somewhere visible meanwhile. Gives up after about a second.
 */
function focusTabWhenShown(quoteId: string) {
  if (!UUID.test(quoteId)) return;
  const selector = `[role="tab"][data-quote-id="${quoteId.toLowerCase()}"]`;
  let tries = 0;
  const step = () => {
    const tab = document.querySelector<HTMLElement>(selector);
    if (tab && tab.getAttribute("aria-selected") === "true") {
      const here = document.activeElement as HTMLElement | null;
      if (!here || here === document.body || here.closest("[hidden]") || !here.isConnected) tab.focus();
      return;
    }
    if (++tries < 40) setTimeout(step, 25);
  };
  setTimeout(step, 0);
}

/**
 * «Institución» of a person or an address the CRM knows none of: a search over the same suggestions
 * (institutions only) and «+ Nueva institución «texto»» with its kind. Optional; it says why it matters.
 */
function InstitutionField({
  picked,
  kind,
  personName = null,
  busy,
  onPick,
  onKind,
}: {
  picked: InstitutionPick | null;
  kind: NewClientKind;
  /** The person's name, for «Persona natural»; null for a bare address. */
  personName?: string | null;
  busy: boolean;
  onPick: (next: InstitutionPick | null) => void;
  onKind: (kind: NewClientKind) => void;
}) {
  const ids = useId();
  const [text, setText] = useState("");
  // «Persona natural (particular)» (owner, 2026-10-22): the person becomes their own client record
  // (kind «individual»), so the quote's case reaches «Enviada» like any other.
  const [persona, setPersona] = useState<string | null>(null);
  const term = text.trim();
  const { found, retry } = useClientSuggestions(term, picked === null);
  const options: Option[] = useMemo(() => {
    if (!found) return [];
    const orgs = (found.data?.organizations ?? []).map((org) => ({
      key: `org:${org.id}`,
      client: { kind: "organization" as const, org, casesKnown: true },
      main: org.name,
      side: org.domains.length ? org.domains.join(", ") : "institución",
    }));
    return [...orgs, { key: "new", client: { kind: "new" as const, name: term.slice(0, NAME_MAX) }, main: `+ Nueva institución «${term}»`, side: null, strong: true }];
  }, [found, term]);
  const listOpen = picked === null && term.length >= MIN_SEARCH;
  const listId = `${ids}-instituciones`;
  const hint = (
    <p className="text-[12.5px] text-ink-muted">
      Una persona natural queda como su propio cliente en el CRM. Se asocia también a la persona y al caso.
    </p>
  );

  if (picked && picked.kind === "new" && kind === "individual") {
    return (
      <div className="space-y-1.5">
        <p className="text-[13px] text-ink-muted">Cliente</p>
        <div className="flex items-center justify-between gap-3 rounded-[10px] border border-line px-3 py-2 text-[14px] text-ink">
          <span className="min-w-0 truncate">
            <span className="font-semibold">{picked.name}</span> · persona natural
          </span>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              onKind("company");
              onPick(null);
            }}
            className="shrink-0 text-[13px] font-semibold text-brand-700 underline"
          >
            Cambiar
          </button>
        </div>
      </div>
    );
  }

  if (persona !== null) {
    return (
      <div className="space-y-1.5">
        <label htmlFor={`${ids}-persona`} className="block text-[13px] text-ink-muted">
          Nombre de la persona natural
        </label>
        <input
          id={`${ids}-persona`}
          type="text"
          value={persona}
          maxLength={NAME_MAX}
          disabled={busy}
          placeholder="Nombre y apellido"
          onChange={(e) => setPersona(e.target.value)}
          className="h-11 w-full min-w-0 rounded-[10px] border-[1.5px] border-line-strong bg-canvas-raised px-3 text-[14.5px] text-ink placeholder:text-ink-faint focus:border-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-600/30 disabled:opacity-60"
        />
        <div className="flex gap-3">
          <button
            type="button"
            disabled={busy || !persona.trim()}
            onClick={() => {
              onKind("individual");
              onPick({ kind: "new", name: persona.trim().slice(0, NAME_MAX) });
              setPersona(null);
            }}
            className="text-[13px] font-semibold text-brand-700 underline disabled:opacity-50"
          >
            Usar como persona natural
          </button>
          <button type="button" disabled={busy} onClick={() => setPersona(null)} className="text-[13px] text-ink-muted underline">
            Cancelar
          </button>
        </div>
      </div>
    );
  }

  if (picked) {
    return (
      <div className="space-y-1.5">
        <p className="text-[13px] text-ink-muted">Institución</p>
        <div className="flex items-center justify-between gap-3 rounded-[10px] border border-line px-3 py-2 text-[14px] text-ink">
          <span className="min-w-0 truncate font-semibold">
            {picked.kind === "new" ? `Nueva: ${picked.name}` : picked.name}
          </span>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setText(picked.name);
              onPick(null);
            }}
            className="shrink-0 text-[13px] font-semibold text-brand-700 underline"
          >
            Cambiar institución
          </button>
        </div>
        {picked.kind === "new" ? (
          <FormField label="Tipo de la nueva institución" htmlFor={`${ids}-tipo`}>
            <SelectInput id={`${ids}-tipo`} value={kind} onChange={(v) => onKind(v as NewClientKind)} options={KIND_OPTIONS} disabled={busy} />
          </FormField>
        ) : null}
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      <label htmlFor={`${ids}-institucion`} className="block text-[13px] text-ink-muted">
        Institución
      </label>
      <input
        id={`${ids}-institucion`}
        type="text"
        role="combobox"
        autoComplete="off"
        aria-expanded={listOpen}
        aria-controls={listId}
        aria-autocomplete="list"
        value={text}
        disabled={busy}
        placeholder="¿De qué institución es?"
        onChange={(e) => setText(e.target.value)}
        className="h-11 w-full min-w-0 rounded-[10px] border-[1.5px] border-line-strong bg-canvas-raised px-3 text-[14.5px] text-ink placeholder:text-ink-faint focus:border-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-600/30 disabled:opacity-60"
      />
      {listOpen ? (
        <SuggestionList
          id={listId}
          label="Instituciones"
          options={options}
          active={-1}
          loading={!found}
          failed={!!found?.failed}
          failedText="No se pudo buscar instituciones."
          onRetry={retry}
          onHover={() => undefined}
          onPick={(o) =>
            onPick(o.client.kind === "organization" ? { kind: "existing", id: o.client.org.id, name: o.client.org.name } : { kind: "new", name: term.slice(0, NAME_MAX) })
          }
        />
      ) : null}
      <button
        type="button"
        disabled={busy}
        onClick={() => setPersona(personName ?? "")}
        className="text-[13px] font-semibold text-brand-700 underline"
      >
        Es persona natural (particular)
      </button>
      {hint}
    </div>
  );
}

function SuggestionList({
  id,
  label = "Clientes",
  options,
  active,
  loading,
  failed,
  failedText = "No se pudo buscar clientes.",
  onRetry,
  onHover,
  onPick,
}: {
  id: string;
  label?: string;
  failedText?: string;
  options: Option[];
  active: number;
  loading: boolean;
  failed: boolean;
  onRetry: () => void;
  onHover: (i: number) => void;
  onPick: (o: Option) => void;
}) {
  return (
    <div className="space-y-1.5">
      {loading ? (
        <p role="status" className="px-1 text-[12.5px] text-ink-muted">
          Buscando…
        </p>
      ) : null}
      {failed ? (
        <p role="alert" className="text-[12.5px] text-bad">
          {failedText}{" "}
          <button type="button" onClick={onRetry} className="font-semibold text-brand-700 underline">
            Reintentar
          </button>
        </p>
      ) : null}
      <ul id={id} role="listbox" aria-label={label} className={options.length ? "max-h-72 overflow-y-auto rounded-[10px] border border-line" : "hidden"}>
        {options.map((o, i) => (
          <li
            key={o.key}
            id={`${id}-${i}`}
            role="option"
            aria-selected={i === active}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => onPick(o)}
            onMouseEnter={() => onHover(i)}
            className={`flex min-h-[44px] cursor-pointer items-center justify-between gap-2.5 px-3 py-2 text-[14px] text-ink ${i ? "border-t border-line" : ""} ${
              i === active ? "bg-brand-50" : ""
            } ${o.strong ? "font-semibold text-brand-700" : ""}`}
          >
            <span className={`min-w-0 truncate ${o.side && o.client.kind === "organization" ? "font-semibold" : ""}`}>{o.main}</span>
            {o.side ? <span className="shrink-0 text-[12.5px] font-normal text-ink-muted">{o.side}</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function CaseRadio({ name, checked, onChoose, children }: { name: string; checked: boolean; onChoose: () => void; children: ReactNode }) {
  return (
    <label
      className={`flex cursor-pointer items-start gap-2.5 rounded-[10px] px-3 py-2.5 text-[14px] text-ink ${
        checked ? "border-[1.5px] border-brand-700" : "border border-line"
      }`}
    >
      <input type="radio" name={name} checked={checked} onChange={onChoose} className="mt-1" />
      <span className="min-w-0">{children}</span>
    </label>
  );
}
