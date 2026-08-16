import { beforeEach, describe, expect, it } from 'vitest';
import { dashboardState } from '/static/js/dashboard/core/state.js';
import { filterTable } from '/static/js/dashboard/filters.js';

describe('dashboard request filtering', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <input id="filter-input" value="matrix">
      <table><tbody id="active-requests-body">
        <tr id="matching" data-title="the matrix" data-type="movie" data-status-low="pending" data-requestedby="neo"></tr>
        <tr id="other" data-title="arrival" data-type="movie" data-status-low="pending" data-requestedby="louise"></tr>
      </tbody></table>
    `;
    dashboardState.mediaFilterState = { active: null };
  });

  it('hides nonmatching dashboard rows', () => {
    filterTable();

    expect(document.querySelector('#matching').style.display).toBe('');
    expect(document.querySelector('#other').style.display).toBe('none');
  });
});
