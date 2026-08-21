"use client";

import { useCallback, useEffect, useState } from "react";
import {
  createSongReference,
  deleteSongReference,
  listSongReferences,
} from "@/lib/api";
import type { SongReference, SongReferenceInput } from "@/types/api";

export interface SongReferencesState {
  references: SongReference[];
  loading: boolean;
  /** A failed request, in words. Never set by a successful empty list. */
  error: string | null;
  /** True while a create or a delete is in flight. */
  saving: boolean;
}

export interface SongReferencesApi extends SongReferencesState {
  add: (input: SongReferenceInput) => Promise<SongReference | null>;
  remove: (referenceId: string) => Promise<void>;
}

/**
 * The caller's own song references, and the two ways to change the collection.
 *
 * Not a poller: references are typed, not measured, so nothing happens to one
 * after it is written and there is nothing to wait on. The list is read once
 * and then kept in step with what the mutations return — `add` from the created
 * reference, `remove` from the remaining collection the server sends back — so
 * neither needs a follow-up read.
 *
 * `add` resolves to `null` rather than throwing when the server refuses, so a
 * form can show the reason inline. A refusal is a normal outcome of a form.
 */
export function useSongReferences(): SongReferencesApi {
  const [state, setState] = useState<SongReferencesState>({
    references: [],
    loading: true,
    error: null,
    saving: false,
  });

  useEffect(() => {
    const controller = new AbortController();

    listSongReferences(controller.signal)
      .then((list) => {
        if (controller.signal.aborted) return;
        setState({
          references: list.references,
          loading: false,
          error: null,
          saving: false,
        });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          references: [],
          loading: false,
          error:
            error instanceof Error
              ? error.message
              : "Your saved songs could not be loaded.",
          saving: false,
        });
      });

    return () => controller.abort();
  }, []);

  const add = useCallback(async (input: SongReferenceInput) => {
    setState((current) => ({ ...current, saving: true, error: null }));
    try {
      const created = await createSongReference(input);
      setState((current) => ({
        ...current,
        // Newest first, matching the order the server lists them in.
        references: [created, ...current.references],
        saving: false,
      }));
      return created;
    } catch (error: unknown) {
      setState((current) => ({
        ...current,
        saving: false,
        error:
          error instanceof Error ? error.message : "That song could not be saved.",
      }));
      return null;
    }
  }, []);

  const remove = useCallback(async (referenceId: string) => {
    setState((current) => ({ ...current, saving: true, error: null }));
    try {
      const remaining = await deleteSongReference(referenceId);
      setState((current) => ({
        ...current,
        references: remaining.references,
        saving: false,
      }));
    } catch (error: unknown) {
      setState((current) => ({
        ...current,
        saving: false,
        error:
          error instanceof Error ? error.message : "That song could not be removed.",
      }));
    }
  }, []);

  return { ...state, add, remove };
}
