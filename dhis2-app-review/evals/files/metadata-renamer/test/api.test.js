import { describe, it, expect, vi, beforeEach } from 'vitest'
import { patchName } from '../src/api.js'
import { makePrivate } from '../src/sharing.js'

describe('api', () => {
    beforeEach(() => {
        global.fetch = vi.fn().mockResolvedValue({ ok: true, status: 200 })
    })

    it('renames via PATCH', async () => {
        await patchName('dataElements', 'fbfJHSPpUQD', 'ANC 1st visit')
        expect(fetch).toHaveBeenCalledWith('/api/dataElements/fbfJHSPpUQD', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: 'ANC 1st visit' }),
        })
    })

    it('makes an object private', async () => {
        await makePrivate('dataElements', 'fbfJHSPpUQD')
        expect(fetch.mock.calls[0][1].method).toBe('PATCH')
        expect(JSON.parse(fetch.mock.calls[0][1].body)[0].path).toBe(
            '/sharing/public'
        )
    })
})
