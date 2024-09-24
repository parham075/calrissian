cwlVersion: v1.2
$namespaces:
  s: https://schema.org/
s:softwareVersion: 1.0.0
schemas:
  - http://schema.org/version/9.0/schemaorg-current-http.rdf
$graph:
  - class: Workflow
    id: main
    label: Access and expose the calrissian and CWL steps logs.
    doc: The goal is to access and expose the calrissian and CWL steps logs.
    requirements:
      - class: InlineJavascriptRequirement
      - class: ScatterFeatureRequirement
    inputs: 
      input_reference: 
        doc: S2 product
        label: S2 product
        type: string[]
    outputs: []
    steps:
      run_script:
        run: "#log_behaviour_scripy"
        scatter: input_reference
        scatterMethod: dotproduct
        in:
          input_reference: input_reference
        out: []
    
  - class: CommandLineTool
    id: log_behaviour_scripy
    baseCommand: ["/bin/bash", "run_me.sh"]
    arguments: []


    inputs:
      input_reference: string
    outputs: []
    requirements:
      InlineJavascriptRequirement: {}
      NetworkAccess:
        networkAccess: true     
      ResourceRequirement:
        coresMax: 1
        ramMax: 1600
      InitialWorkDirRequirement:
        listing:
          - entryname: run_me.sh
            entry: |-
              pip install loguru
              python main.py

          - entryname: main.py
            entry: |-
              from loguru import logger
              import os
              import sys
              logger.info("Enhancement of the access to logs is start")
              
              input_reference = "$(inputs.input_reference)"
              if "s3" not in input_reference:
                logger.info("input reference is not in s3 bucket file system")
                sys.exit(1)
              else:
                logger.info("The input reference is valid")
              