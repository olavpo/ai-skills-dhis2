// Remove public access from an object, keeping user/group sharing intact.
export async function makePrivate(type, id) {
    const res = await fetch(`/api/${type}/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json-patch+json' },
        body: JSON.stringify([
            { op: 'replace', path: '/sharing/public', value: '--------' },
        ]),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return true
}
