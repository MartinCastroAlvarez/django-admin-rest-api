---
name: Feature request
about: Propose new wire-format coverage of an existing Django admin behavior
title: ""
labels: enhancement
---

## The Django admin behavior you want exposed

(Which `ModelAdmin` hook / which HTML admin page is missing a JSON
equivalent?)

## Out of scope reminder

This library does **not** add new auth, new permissions, new
validation, or new business logic. If your proposal involves any of
those, it likely belongs in a consumer (your project, or
[`django-admin-react`](https://github.com/MartinCastroAlvarez/django-admin-react))
rather than here.

## Proposed wire format

(JSON shape, HTTP verb, query parameters, status codes.)

## Permission model

(Which `ModelAdmin.has_*_permission` gate applies?)
