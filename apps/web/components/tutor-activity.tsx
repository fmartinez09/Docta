"use client";

import { useId, useState } from "react";
import type { ActivityBlock } from "../lib/tutor-contract";

/** An activity is local to this rendered message; no selection is sent to the model. */
export function TutorActivity({ activity }: { activity: ActivityBlock }) {
  const name = useId();
  const [selected, setSelected] = useState("");
  const [checked, setChecked] = useState(false);
  const [showHint, setShowHint] = useState(false);
  const correct = selected === activity.correct_option_id;
  return (
    <section className="rich-activity" aria-label="Actividad de razonamiento">
      <h3>Ahora te toca razonar</h3>
      <fieldset>
        <legend>{activity.question}</legend>
        {activity.options.map((option, index) => (
          <label className="rich-option" key={option.id}>
            <input
              type="radio"
              name={name}
              value={option.id}
              checked={selected === option.id}
              onChange={() => { setSelected(option.id); setChecked(false); }}
            />
            <span>{String.fromCharCode(65 + index)}. {option.text}</span>
          </label>
        ))}
      </fieldset>
      <div className="rich-activity-actions">
        <button type="button" className="rich-action" disabled={!selected} onClick={() => setChecked(true)}>
          Comprobar mi respuesta
        </button>
        <button type="button" className="rich-action" onClick={() => setShowHint(true)}>
          Necesito una pista
        </button>
      </div>
      {showHint && <p className="rich-activity-hint"><strong>Pista:</strong> {activity.hint}</p>}
      {checked && (
        <p className="rich-activity-feedback" role="status">
          {correct ? <>Correcto. {activity.explanation}</> : "Aún no es correcto. Inténtalo de nuevo."}
        </p>
      )}
    </section>
  );
}
