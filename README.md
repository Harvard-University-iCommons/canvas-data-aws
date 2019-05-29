# Canvas Data on AWS

This repository contains artifacts necessary to create a Canvas Data Warehouse on AWS. See the [[tutorial page|tutorial]] for detailed instructions on how to set up your own environment.

## CloudFormation template

A CloudFormation template, `cloud_formation/canvas_data_aws/yaml` is used to create all of the AWS infrastructure components to build the warehouse (see the template for details).

## Lambda functions

The Python code for two Lambda functions is included in the `lambda` directory. This code is used by the CloudFormation template.
