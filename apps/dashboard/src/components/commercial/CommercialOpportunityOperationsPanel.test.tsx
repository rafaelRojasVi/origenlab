import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import {
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import {
  cancelCommercialTask,
  completeCommercialTask,
  createCommercialActivity,
  createCommercialTask,
  fetchCommercialOpportunityActivities,
  fetchCommercialOpportunityOperatorState,
  fetchCommercialOpportunityTasks,
  setCommercialOpportunityOperatorState,
} from "../../api/commercialOperationsClient";

import { CommercialOpportunityOperationsPanel } from "./CommercialOpportunityOperationsPanel";


vi.mock(
  "../../api/commercialOperationsClient",
  () => ({
    cancelCommercialTask: vi.fn(),
    completeCommercialTask: vi.fn(),
    createCommercialActivity: vi.fn(),
    createCommercialTask: vi.fn(),
    fetchCommercialOpportunityActivities: vi.fn(),
    fetchCommercialOpportunityOperatorState: vi.fn(),
    fetchCommercialOpportunityTasks: vi.fn(),
    setCommercialOpportunityOperatorState: vi.fn(),
  }),
);


const OPPORTUNITY_ID =
  "o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

const TASK_ID =
  "task_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

const NOW =
  "2026-08-24T14:00:00Z";


const OPEN_TASK = {
  task_id: TASK_ID,
  opportunity_id: OPPORTUNITY_ID,
  account_id: null,
  contact_id: null,
  title: "Llamar al cliente",
  status: "open" as const,
  priority: "high" as const,
  due_at: "2026-08-25T14:00:00Z",
  owner_key: null,
  version: 3,
  created_by: "tatiana@origenlab.cl",
  updated_by: "tatiana@origenlab.cl",
  completed_at: null,
  created_at: NOW,
  updated_at: NOW,
};


describe(
  "CommercialOpportunityOperationsPanel",
  () => {
    beforeEach(() => {
      vi.clearAllMocks();

      vi.mocked(
        fetchCommercialOpportunityOperatorState,
      ).mockResolvedValue({
        state: null,
      });

      vi.mocked(
        fetchCommercialOpportunityActivities,
      ).mockResolvedValue({
        items: [],
      });

      vi.mocked(
        fetchCommercialOpportunityTasks,
      ).mockResolvedValue({
        items: [],
      });
    });


    it("loads durable human CRM state separately from PR3", async () => {
      render(
        <CommercialOpportunityOperationsPanel
          opportunityId={OPPORTUNITY_ID}
        />,
      );

      expect(
        await screen.findByText(
          "Sin revisión humana registrada",
        ),
      ).toBeTruthy();

      expect(
        fetchCommercialOpportunityOperatorState,
      ).toHaveBeenCalledWith(
        OPPORTUNITY_ID,
      );

      expect(
        fetchCommercialOpportunityActivities,
      ).toHaveBeenCalledWith(
        OPPORTUNITY_ID,
      );

      expect(
        fetchCommercialOpportunityTasks,
      ).toHaveBeenCalledWith(
        OPPORTUNITY_ID,
      );
    });


    it("creates first human state with expected version zero", async () => {
      vi.mocked(
        setCommercialOpportunityOperatorState,
      ).mockResolvedValue({
        opportunity_id: OPPORTUNITY_ID,
        confirmation_status: "confirmed",
        manual_stage: "follow_up",
        owner_key: null,
        version: 1,
        created_by: "tatiana@origenlab.cl",
        updated_by: "tatiana@origenlab.cl",
        created_at: NOW,
        updated_at: NOW,
      });

      render(
        <CommercialOpportunityOperationsPanel
          opportunityId={OPPORTUNITY_ID}
        />,
      );

      await screen.findByText(
        "Sin revisión humana registrada",
      );

      fireEvent.change(
        screen.getByLabelText(
          "Revisión humana",
        ),
        {
          target: {
            value: "confirmed",
          },
        },
      );

      fireEvent.change(
        screen.getByLabelText(
          "Etapa operativa manual",
        ),
        {
          target: {
            value: "follow_up",
          },
        },
      );

      fireEvent.click(
        screen.getByRole(
          "button",
          {
            name: "Guardar estado",
          },
        ),
      );

      await waitFor(() => {
        expect(
          setCommercialOpportunityOperatorState,
        ).toHaveBeenCalledWith(
          OPPORTUNITY_ID,
          {
            confirmation_status:
              "confirmed",
            manual_stage:
              "follow_up",
            owner_key: null,
            expected_version: 0,
          },
        );
      });

      expect(
        await screen.findByText(
          "Confirmada · versión 1",
        ),
      ).toBeTruthy();
    });


    it("logs a human activity", async () => {
      vi.mocked(
        createCommercialActivity,
      ).mockResolvedValue({
        activity_id: "act_1",
        opportunity_id: OPPORTUNITY_ID,
        account_id: null,
        contact_id: null,
        activity_type: "whatsapp",
        occurred_at: NOW,
        summary:
          "Cliente pidió seguimiento",
        detail: null,
        created_by:
          "tatiana@origenlab.cl",
        created_at: NOW,
      });

      render(
        <CommercialOpportunityOperationsPanel
          opportunityId={OPPORTUNITY_ID}
        />,
      );

      await screen.findByText(
        "Sin revisión humana registrada",
      );

      fireEvent.change(
        screen.getByLabelText(
          "Tipo de actividad",
        ),
        {
          target: {
            value: "whatsapp",
          },
        },
      );

      fireEvent.change(
        screen.getByLabelText(
          "Resumen de actividad",
        ),
        {
          target: {
            value:
              "Cliente pidió seguimiento",
          },
        },
      );

      fireEvent.click(
        screen.getByRole(
          "button",
          {
            name: "Registrar actividad",
          },
        ),
      );

      await waitFor(() => {
        expect(
          createCommercialActivity,
        ).toHaveBeenCalledWith(
          expect.objectContaining({
            opportunity_id:
              OPPORTUNITY_ID,
            activity_type:
              "whatsapp",
            summary:
              "Cliente pidió seguimiento",
          }),
          expect.stringMatching(
            /^activity:/,
          ),
        );
      });

      expect(
        await screen.findByText(
          "Cliente pidió seguimiento",
        ),
      ).toBeTruthy();
    });


    it("creates a follow-up task", async () => {
      vi.mocked(
        createCommercialTask,
      ).mockResolvedValue(
        OPEN_TASK,
      );

      render(
        <CommercialOpportunityOperationsPanel
          opportunityId={OPPORTUNITY_ID}
        />,
      );

      await screen.findByText(
        "Sin revisión humana registrada",
      );

      fireEvent.change(
        screen.getByLabelText(
          "Seguimiento",
        ),
        {
          target: {
            value:
              "Llamar al cliente",
          },
        },
      );

      fireEvent.change(
        screen.getByLabelText(
          "Prioridad",
        ),
        {
          target: {
            value: "high",
          },
        },
      );

      fireEvent.click(
        screen.getByRole(
          "button",
          {
            name: "Crear seguimiento",
          },
        ),
      );

      await waitFor(() => {
        expect(
          createCommercialTask,
        ).toHaveBeenCalledWith(
          {
            opportunity_id:
              OPPORTUNITY_ID,
            title:
              "Llamar al cliente",
            priority: "high",
            due_at: null,
          },
          expect.stringMatching(
            /^task:/,
          ),
        );
      });

      expect(
        await screen.findByText(
          "Llamar al cliente",
        ),
      ).toBeTruthy();
    });


    it("completes an open task with optimistic version", async () => {
      vi.mocked(
        fetchCommercialOpportunityTasks,
      ).mockResolvedValue({
        items: [OPEN_TASK],
      });

      vi.mocked(
        completeCommercialTask,
      ).mockResolvedValue({
        ...OPEN_TASK,
        status: "done",
        version: 4,
        completed_at:
          "2026-08-24T15:00:00Z",
        updated_at:
          "2026-08-24T15:00:00Z",
      });

      render(
        <CommercialOpportunityOperationsPanel
          opportunityId={OPPORTUNITY_ID}
        />,
      );

      const complete =
        await screen.findByRole(
          "button",
          {
            name: "Completar",
          },
        );

      fireEvent.click(complete);

      await waitFor(() => {
        expect(
          completeCommercialTask,
        ).toHaveBeenCalledWith(
          TASK_ID,
          {
            expected_version: 3,
          },
        );
      });

      expect(
        await screen.findByText(
          /Completada/,
        ),
      ).toBeTruthy();

      expect(
        screen.queryByRole(
          "button",
          {
            name: "Completar",
          },
        ),
      ).toBeNull();
    });


    it("cancels an open task with optimistic version", async () => {
      vi.mocked(
        fetchCommercialOpportunityTasks,
      ).mockResolvedValue({
        items: [OPEN_TASK],
      });

      vi.mocked(
        cancelCommercialTask,
      ).mockResolvedValue({
        ...OPEN_TASK,
        status: "cancelled",
        version: 4,
        updated_at:
          "2026-08-24T15:00:00Z",
      });

      render(
        <CommercialOpportunityOperationsPanel
          opportunityId={OPPORTUNITY_ID}
        />,
      );

      fireEvent.click(
        await screen.findByRole(
          "button",
          {
            name: "Cancelar",
          },
        ),
      );

      await waitFor(() => {
        expect(
          cancelCommercialTask,
        ).toHaveBeenCalledWith(
          TASK_ID,
          {
            expected_version: 3,
          },
        );
      });

      expect(
        await screen.findByText(
          /Cancelada/,
        ),
      ).toBeTruthy();
    });
  },
);


describe(
  "CommercialOpportunityOperationsPanel idempotent retries",
  () => {
    beforeEach(() => {
      vi.clearAllMocks();

      vi.mocked(
        fetchCommercialOpportunityOperatorState,
      ).mockResolvedValue({
        state: null,
      });

      vi.mocked(
        fetchCommercialOpportunityActivities,
      ).mockResolvedValue({
        items: [],
      });

      vi.mocked(
        fetchCommercialOpportunityTasks,
      ).mockResolvedValue({
        items: [],
      });
    });


    it("reuses activity command and key after a failed response", async () => {
      vi.mocked(
        createCommercialActivity,
      )
        .mockRejectedValueOnce(
          new Error("Network response lost"),
        )
        .mockResolvedValueOnce({
          activity_id: "act_retry",
          opportunity_id: OPPORTUNITY_ID,
          account_id: null,
          contact_id: null,
          activity_type: "call",
          occurred_at: NOW,
          summary: "Llamar cliente",
          detail: null,
          created_by:
            "tatiana@origenlab.cl",
          created_at: NOW,
        });

      render(
        <CommercialOpportunityOperationsPanel
          opportunityId={OPPORTUNITY_ID}
        />,
      );

      await screen.findByText(
        "Sin revisión humana registrada",
      );

      fireEvent.change(
        screen.getByLabelText(
          "Resumen de actividad",
        ),
        {
          target: {
            value: "Llamar cliente",
          },
        },
      );

      const button = screen.getByRole(
        "button",
        {
          name: "Registrar actividad",
        },
      );

      fireEvent.click(button);

      await waitFor(() => {
        expect(
          createCommercialActivity,
        ).toHaveBeenCalledTimes(1);
      });

      const firstCall = vi.mocked(
        createCommercialActivity,
      ).mock.calls[0];

      fireEvent.click(button);

      await waitFor(() => {
        expect(
          createCommercialActivity,
        ).toHaveBeenCalledTimes(2);
      });

      const secondCall = vi.mocked(
        createCommercialActivity,
      ).mock.calls[1];

      expect(secondCall[1]).toBe(
        firstCall[1],
      );

      // occurred_at must also remain identical,
      // otherwise the server fingerprint would differ.
      expect(secondCall[0]).toEqual(
        firstCall[0],
      );
    });


    it("uses a new activity key when the form changes after failure", async () => {
      vi.mocked(
        createCommercialActivity,
      )
        .mockRejectedValueOnce(
          new Error("Network response lost"),
        )
        .mockResolvedValueOnce({
          activity_id: "act_changed",
          opportunity_id: OPPORTUNITY_ID,
          account_id: null,
          contact_id: null,
          activity_type: "note",
          occurred_at: NOW,
          summary: "Segundo intento distinto",
          detail: null,
          created_by:
            "tatiana@origenlab.cl",
          created_at: NOW,
        });

      render(
        <CommercialOpportunityOperationsPanel
          opportunityId={OPPORTUNITY_ID}
        />,
      );

      await screen.findByText(
        "Sin revisión humana registrada",
      );

      const summary = screen.getByLabelText(
        "Resumen de actividad",
      );

      fireEvent.change(
        summary,
        {
          target: {
            value: "Primer intento",
          },
        },
      );

      const button = screen.getByRole(
        "button",
        {
          name: "Registrar actividad",
        },
      );

      fireEvent.click(button);

      await waitFor(() => {
        expect(
          createCommercialActivity,
        ).toHaveBeenCalledTimes(1);
      });

      const firstKey = vi.mocked(
        createCommercialActivity,
      ).mock.calls[0][1];

      fireEvent.change(
        summary,
        {
          target: {
            value:
              "Segundo intento distinto",
          },
        },
      );

      fireEvent.click(button);

      await waitFor(() => {
        expect(
          createCommercialActivity,
        ).toHaveBeenCalledTimes(2);
      });

      const secondKey = vi.mocked(
        createCommercialActivity,
      ).mock.calls[1][1];

      expect(secondKey).not.toBe(
        firstKey,
      );
    });


    it("reuses task key after a failed response", async () => {
      vi.mocked(
        createCommercialTask,
      )
        .mockRejectedValueOnce(
          new Error("Network response lost"),
        )
        .mockResolvedValueOnce(
          OPEN_TASK,
        );

      render(
        <CommercialOpportunityOperationsPanel
          opportunityId={OPPORTUNITY_ID}
        />,
      );

      await screen.findByText(
        "Sin revisión humana registrada",
      );

      fireEvent.change(
        screen.getByLabelText(
          "Seguimiento",
        ),
        {
          target: {
            value: "Llamar al cliente",
          },
        },
      );

      const button = screen.getByRole(
        "button",
        {
          name: "Crear seguimiento",
        },
      );

      fireEvent.click(button);

      await waitFor(() => {
        expect(
          createCommercialTask,
        ).toHaveBeenCalledTimes(1);
      });

      const firstCall = vi.mocked(
        createCommercialTask,
      ).mock.calls[0];

      fireEvent.click(button);

      await waitFor(() => {
        expect(
          createCommercialTask,
        ).toHaveBeenCalledTimes(2);
      });

      const secondCall = vi.mocked(
        createCommercialTask,
      ).mock.calls[1];

      expect(secondCall[1]).toBe(
        firstCall[1],
      );

      expect(secondCall[0]).toEqual(
        firstCall[0],
      );
    });
  },
);
