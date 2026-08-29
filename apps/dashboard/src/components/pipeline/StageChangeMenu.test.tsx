import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StageChangeMenu } from "./StageChangeMenu";

describe("StageChangeMenu", () => {
  it("renders all eight stages as options and reports the current one", () => {
    render(<StageChangeMenu stage="qualifying" onChange={vi.fn()} />);

    const select = screen.getByLabelText("Cambiar etapa") as HTMLSelectElement;
    expect(select.value).toBe("qualifying");
    expect(select.options).toHaveLength(8);
  });

  it("calls onChange with the newly selected stage", () => {
    const onChange = vi.fn();
    render(<StageChangeMenu stage="new" onChange={onChange} />);

    fireEvent.change(screen.getByLabelText("Cambiar etapa"), {
      target: { value: "negotiating" },
    });

    expect(onChange).toHaveBeenCalledWith("negotiating");
  });

  it("does not let a click bubble to an ancestor click handler", () => {
    const ancestorClick = vi.fn();
    render(
      // eslint-disable-next-line jsx-a11y/no-static-element-interactions, jsx-a11y/click-events-have-key-events
      <div onClick={ancestorClick}>
        <StageChangeMenu stage="new" onChange={vi.fn()} />
      </div>,
    );

    fireEvent.click(screen.getByLabelText("Cambiar etapa"));
    expect(ancestorClick).not.toHaveBeenCalled();
  });

  it("renders a closed, non-interactive badge for terminal stages instead of a control", () => {
    render(<StageChangeMenu stage="won" onChange={vi.fn()} />);

    expect(screen.queryByLabelText("Cambiar etapa")).not.toBeInTheDocument();
    expect(screen.getByText("Ganada · cerrada")).toBeInTheDocument();
  });

  it("disables the control while a transition is pending", () => {
    render(<StageChangeMenu stage="new" onChange={vi.fn()} disabled />);
    expect(screen.getByLabelText("Cambiar etapa")).toBeDisabled();
  });
});
