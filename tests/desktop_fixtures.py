"""Synthetic consortium identity fixtures. Never real research records."""
from catalyst_desktop.traceability import LABS, lab_id


def uid(lab, kind, number=1):
    return f'CAT-{LABS[lab_id(lab)][0]}-{kind}-{number:032x}'


def physical_context(lab='Rochester', number=1, **overrides):
    result = dict(specimenId=uid(lab, 'SMP', number), runId='SYNTHETIC-1', acquiredBy='Synthetic operator',
        acquiredAt='2026-09-12', datasetId=uid(lab, 'DS', number), submittingLab=lab, acquisitionLab=lab, processingLab=lab,
        localSampleId='001', methodId='SYNTHETIC-METHOD', methodVersion='1',
        batchId=uid(lab, 'BAT', number), originLab=lab, localBatchId='Synthetic batch ' + str(number),
        procedureId='SYNTHETIC-SOP', procedureVersion='1', procedureReference='Synthetic shared procedure document v1',
        synthesisExecutionId=uid(lab, 'SYN', number), synthesizedAt='2026-09-10',
        synthesisRecord='Synthetic ELN record ' + lab + ' ' + str(number), synthesisDeviations='none',
        sampleCreatedLab=lab, materialKind='batch material', materialState='as synthesized',
        parentSampleId='', synthesisMethod='Synthetic protocol', scale='laboratory')
    result.update(overrides)
    return result


def computational_context(**overrides):
    result = dict(specimenId=uid('VT', 'MDL'), runId='SYNTHETIC-CALC', acquiredBy='Synthetic operator',
        acquiredAt='2026-09-12', datasetId=uid('VT', 'DS'), submittingLab='VT', acquisitionLab='VT', processingLab='VT',
        localSampleId='model-001', methodId='SYNTHETIC-CALCULATION', methodVersion='1', modelCreatedLab='VT',
        modelDescription='Synthetic theoretical structure', modelRelation='no physical link', relatedSampleIds='',
        softwareVersion='Synthetic program v1', calculationMethod='Synthetic method', inputStructure='Synthetic structure v1',
        parameters='Synthetic inputs v1', environment='Synthetic environment v1', convergence='Synthetic converged run',
        quantityBasis='Total electronic energy in eV per configuration, stated synthetic reference')
    result.update(overrides)
    return result
