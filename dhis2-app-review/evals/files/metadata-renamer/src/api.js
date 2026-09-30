const BASE = '/api'

export async function getDataElements(query) {
    const res = await fetch(
        `${BASE}/dataElements?filter=name:ilike:${encodeURIComponent(query)}&fields=id,name,shortName&paging=false`
    )
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const json = await res.json()
    return json.dataElements
}

export async function getIndicators(query) {
    const res = await fetch(
        `${BASE}/indicators?filter=name:ilike:${encodeURIComponent(query)}&fields=id,name,shortName&paging=false`
    )
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const json = await res.json()
    return json.indicators
}

export async function getDataSets(query) {
    const res = await fetch(
        `${BASE}/dataSets?filter=name:ilike:${encodeURIComponent(query)}&fields=id,name,shortName&paging=false`
    )
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const json = await res.json()
    return json.dataSets
}

// Rename a single object with a partial update.
export async function patchName(type, id, name) {
    const res = await fetch(`${BASE}/${type}/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
    })
    if (res.status !== 200) {
        throw new Error(`Rename failed for ${id}: HTTP ${res.status}`)
    }
    return true
}

// Create a copy of a data element; returns the new uid.
export async function duplicateDataElement(de) {
    const res = await fetch(`${BASE}/dataElements`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            name: `${de.name} (copy)`,
            shortName: `${de.shortName} (copy)`.slice(0, 50),
            valueType: 'NUMBER',
            domainType: 'AGGREGATE',
            aggregationType: 'SUM',
        }),
    })
    const body = await res.json()
    if (body.response && body.response.uid) {
        return body.response.uid
    }
    throw new Error('Duplicate failed')
}
