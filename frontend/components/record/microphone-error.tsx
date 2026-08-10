"use client";

import { Button } from "@/components/ui/button";
import { messageForMicrophoneError } from "@/lib/microphone-errors";

interface MicrophoneErrorProps {
  code: string;
  onRetry: () => void;
}

/**
 * A microphone failure, in one sentence and one action.
 *
 * The copy is looked up centrally by code — nothing the browser threw reaches
 * this component, and no branch on the code lives here.
 */
export function MicrophoneError({ code, onRetry }: MicrophoneErrorProps) {
  return (
    <div
      role="alert"
      className="fade-in rounded-xl border border-danger/40 bg-danger/5 p-5"
    >
      <p className="text-sm font-medium text-danger">
        Could not use your microphone
      </p>
      <p className="mt-1 text-sm text-muted">
        {messageForMicrophoneError(code)}
      </p>
      <div className="mt-4">
        <Button onClick={onRetry}>Try again</Button>
      </div>
    </div>
  );
}
