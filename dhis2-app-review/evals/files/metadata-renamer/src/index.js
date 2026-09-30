import { getDataElements, patchName, duplicateDataElement } from './api.js'
import { makePrivate } from './sharing.js'

let current = []
const $ = (id) => document.getElementById(id)

function selected() {
    return current.filter((de) => $(`cb-${de.id}`)?.checked)
}

function render() {
    $('results').innerHTML = current
        .map(
            (de) =>
                `<li><input type="checkbox" id="cb-${de.id}"> ${de.name}</li>`
        )
        .join('')
}

$('search').addEventListener('click', async () => {
    current = await getDataElements($('q').value)
    render()
})

$('rename').addEventListener('click', async () => {
    const prefix = $('prefix').value
    let ok = 0
    for (const de of selected()) {
        try {
            await patchName('dataElements', de.id, `${prefix}${de.name}`)
            ok++
        } catch (e) {
            console.error(e)
        }
    }
    $('status').textContent = `Renamed ${ok} data elements`
})

$('private').addEventListener('click', async () => {
    const items = selected()
    for (const de of items) {
        await makePrivate('dataElements', de.id)
    }
    $('status').textContent = `${items.length} data elements are now private`
})

$('copy').addEventListener('click', async () => {
    const uids = []
    for (const de of selected()) {
        uids.push(await duplicateDataElement(de))
    }
    $('status').textContent = `Created ${uids.length} copies: ${uids.join(', ')}`
})
