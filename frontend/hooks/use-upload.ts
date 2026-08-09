"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, uploadRecording, type UploadHandle } from "@/lib/api";
import { validateFileForUpload } from "@/lib/audio-validation";
import { messageForErrorCode } from "@/lib/upload-errors";
import type { PublicConfig, Recording } from "@/types/api";

/**
 * The upload flow as an explicit state machine.
 *
 * `selected` never transitions to `uploading` on its own — the user confirms.
 * `errorKind` separates a client pre-check from a server rejection so the UI
 * can say which happened without re-deriving it from the message.
 */
export type UploadState =
  | { status: "idle" }
  | { status: "selected"; file: File }
  | { status: "uploading"; file: File; progress: number | null }
  | { status: "success"; recording: Recording }
  | {
      status: "error";
      file: File | null;
      message: string;
      errorKind: "validation" | "server";
    };

export interface UploadController {
  state: UploadState;
  selectFile: (file: File) => void;
  clear: () => void;
  startUpload: () => void;
  cancelUpload: () => void;
}

export function useUpload(config: PublicConfig | null): UploadController {
  const [state, setState] = useState<UploadState>({ status: "idle" });
  const handleRef = useRef<UploadHandle | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      handleRef.current?.cancel();
    };
  }, []);

  const selectFile = useCallback(
    (file: File) => {
      const verdict = validateFileForUpload(file, {
        supportedExtensions: config?.supported_extensions,
        maxBytes: config?.max_audio_size_bytes,
      });

      if (!verdict.ok) {
        setState({
          status: "error",
          file: null,
          message: messageForErrorCode(verdict.reason),
          errorKind: "validation",
        });
        return;
      }
      setState({ status: "selected", file });
    },
    [config],
  );

  const clear = useCallback(() => {
    handleRef.current?.cancel();
    handleRef.current = null;
    setState({ status: "idle" });
  }, []);

  const startUpload = useCallback(() => {
    const file =
      state.status === "selected"
        ? state.file
        : state.status === "error"
          ? state.file
          : null;
    if (!file) return;

    setState({ status: "uploading", file, progress: 0 });

    const handle = uploadRecording(file, (fraction) => {
      if (!mountedRef.current) return;
      setState((current) =>
        current.status === "uploading"
          ? { ...current, progress: fraction }
          : current,
      );
    });
    handleRef.current = handle;

    handle.result
      .then((recording) => {
        if (!mountedRef.current) return;
        handleRef.current = null;
        setState({ status: "success", recording });
      })
      .catch((error: unknown) => {
        if (!mountedRef.current) return;
        handleRef.current = null;

        // Cancelling is a user action, not a failure to report.
        if (error instanceof ApiError && error.code === "UPLOAD_CANCELLED") {
          setState({ status: "selected", file });
          return;
        }

        const code = error instanceof ApiError ? error.code : undefined;
        const serverMessage =
          error instanceof ApiError ? error.message : undefined;
        setState({
          status: "error",
          file,
          message: messageForErrorCode(code, serverMessage),
          errorKind: "server",
        });
      });
  }, [state]);

  const cancelUpload = useCallback(() => {
    handleRef.current?.cancel();
  }, []);

  return { state, selectFile, clear, startUpload, cancelUpload };
}
