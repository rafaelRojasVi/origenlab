import { useState } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import { ModelSheetForm } from "../authoring/ModelSheetForm";
import type { CrmSection } from "../crmRoute";
import { Button, EmptyState, FormField, PageHeader, Panel, ResourceGate, TextInput } from "../ui";
import { useResource } from "../useResource";
import { SLUG_PATTERN } from "../web/modelSheet";
import { fetchModelSheet } from "../web/webPublishApi";

type Navigate = (section: CrmSection, id?: string | null) => void;

/**
 * «Catálogo web» (the page is shown to admins only; the API itself accepts any writing role): the sheet of one equipment model of origenlab.cl. The page reads the
 * stored sheet first (so an edit never starts from a guess), lets an admin change it and publishes it
 * to the site's repository, then follows the deployment. The product catalog search has no web model
 * ids, so the model is named by its id.
 */
export function ModelSheetPage({ id, navigate }: { id: string | null; navigate: Navigate }) {
  const { session } = useAuthSession();
  const isAdmin = session.kind === "signed_in" && session.operator.role === "admin";
  const [typed, setTyped] = useState(id ?? "");
  const wanted = typed.trim().toLowerCase();
  const valid = SLUG_PATTERN.test(wanted);

  return (
    <div className="space-y-4">
      <PageHeader title="Catálogo web" subtitle="Fichas de equipos de origenlab.cl: lo que publiques aquí cambia el sitio público." />
      {!isAdmin ? (
        <EmptyState title="Sólo administración">Las fichas del sitio las edita sólo quien administra el panel.</EmptyState>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
          <Panel title="Modelo" bodyClassName="p-3">
            <form
              className="space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (valid) navigate("catalogo-web", wanted);
              }}
            >
              <FormField
                label="Identificador del modelo"
                htmlFor="model-id"
                hint="Como en la dirección de la ficha (minúsculas, números y guiones): up400st."
                error={wanted && !valid ? "Sólo minúsculas, números y guiones." : null}
              >
                <TextInput id="model-id" value={typed} onChange={setTyped} placeholder="up400st" />
              </FormField>
              <Button type="submit" variant="primary" disabled={!valid}>
                Cargar
              </Button>
            </form>
          </Panel>
          <div className="min-w-0">{id ? <Loaded key={id} modelId={id} /> : <EmptyState title="Elige un modelo">Escribe su identificador y pulsa «Cargar»; si no tiene ficha, empiezas una nueva.</EmptyState>}</div>
        </div>
      )}
    </div>
  );
}

function Loaded({ modelId }: { modelId: string }) {
  const [state, reload] = useResource(() => fetchModelSheet(modelId), [modelId]);
  // A 404 that is not «sheet_not_found» means the API has no publishing route here.
  if (state.kind === "unavailable") {
    return (
      <EmptyState title="Publicar web no está activado">
        La ficha no se puede leer ni publicar desde este entorno: falta activar «Publicar web» en el servidor.
      </EmptyState>
    );
  }
  if (state.kind === "error") {
    return (
      <div role="alert" className="rounded-lg border border-bad/30 bg-bad-bg px-4 py-4">
        <p className="text-[13px] font-semibold text-bad">No se pudo leer la ficha</p>
        <p className="mt-1 text-xs text-ink-muted">GitHub no respondió o la ficha no se pudo leer. Nada se cambió; vuelve a intentar.</p>
        <Button className="mt-2" onClick={reload}>
          Reintentar
        </Button>
      </div>
    );
  }
  return (
    <ResourceGate state={state} reload={reload}>
      {(read) => <ModelSheetForm modelId={modelId} read={read} />}
    </ResourceGate>
  );
}
