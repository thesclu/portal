import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'

import { $api, messageOf } from '../../api/query'
import { PageHeader } from '../../app/layout'
import { DialogField, RaisedInput, RaisedTextarea } from '../../components/Fields'

/**
 * Suggest a request (188): a requester says what they want to find out, which agency if they
 * know, and why it matters; staff take it up or decline it from Tasks, and the requester sees
 * what happened under My requests. The agency is the requester's own words (they cannot list
 * the directory), so it is a line of text rather than a menu.
 */
export function Suggest() {
  const client = useQueryClient()
  const navigate = useNavigate()
  const suggest = $api.useMutation('post', '/api/core/suggestions')
  const [what, setWhat] = useState('')
  const [agency, setAgency] = useState('')
  const [why, setWhy] = useState('')

  async function submit(event: FormEvent) {
    event.preventDefault()
    try {
      await suggest.mutateAsync({
        body: { what: what.trim(), agency_hint: agency.trim(), why: why.trim() },
      })
      await client.invalidateQueries({ queryKey: ['get', '/api/core/suggestions'] })
      navigate('/my-requests')
    } catch {
      /* shown below */
    }
  }

  return (
    <div className="flex flex-col leading-[normal]">
      <PageHeader title="Suggest a request" />
      <div className="flex min-w-0 flex-col gap-3.5 px-2 pb-2">
        <section className="overflow-hidden rounded-16 bg-card shadow-float">
          <form
            className="flex max-w-[640px] flex-col gap-3.5 px-[22px] py-5"
            onSubmit={(event) => void submit(event)}
          >
            <DialogField label="What do you want to find out?" required>
              <RaisedTextarea
                required
                autoFocus
                minHeight={70}
                rows={1}
                value={what}
                onChange={(event) => setWhat(event.target.value)}
              />
            </DialogField>
            <DialogField label="Which agency, if you know">
              <RaisedInput
                compact
                value={agency}
                onChange={(event) => setAgency(event.target.value)}
              />
            </DialogField>
            <DialogField label="Why it matters">
              <RaisedTextarea
                minHeight={52}
                rows={1}
                value={why}
                onChange={(event) => setWhy(event.target.value)}
              />
            </DialogField>
            {suggest.isError ? (
              <p role="alert" className="text-12 text-danger">
                {messageOf(suggest.error)}
              </p>
            ) : null}
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={suggest.isPending}
                aria-busy={suggest.isPending || undefined}
                className="box-border inline-flex h-8 items-center gap-1.5 rounded-pill bg-brand px-3.5 text-125 font-medium whitespace-nowrap text-white hover:bg-brand-dark disabled:opacity-55"
              >
                Submit
              </button>
            </div>
          </form>
        </section>
      </div>
    </div>
  )
}
