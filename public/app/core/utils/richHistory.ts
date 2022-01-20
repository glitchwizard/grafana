// Libraries
import { isEqual, omit } from 'lodash';

// Services & Utils
import { DataQuery, DataSourceApi, dateTimeFormat, urlUtil, ExploreUrlState } from '@grafana/data';
import store from 'app/core/store';
import { dispatch } from 'app/store/store';
import { notifyApp } from 'app/core/actions';
import { createErrorNotification, createWarningNotification } from 'app/core/copy/appNotification';

// Types
import { RichHistoryQuery } from 'app/types/explore';
import { serializeStateToUrlParam } from '@grafana/data/src/utils/url';
import { getDataSourceSrv } from '@grafana/runtime';
import { getRichHistoryService } from '../history/richHistoryServiceProvider';
import { RICH_HISTORY_KEY } from '../history/richHistoryLocalStorageService';

export const RICH_HISTORY_SETTING_KEYS = {
  retentionPeriod: 'grafana.explore.richHistory.retentionPeriod',
  starredTabAsFirstTab: 'grafana.explore.richHistory.starredTabAsFirstTab',
  activeDatasourceOnly: 'grafana.explore.richHistory.activeDatasourceOnly',
  datasourceFilters: 'grafana.explore.richHistory.datasourceFilters',
};

export enum SortOrder {
  Descending = 'Descending',
  Ascending = 'Ascending',
  DatasourceAZ = 'Datasource A-Z',
  DatasourceZA = 'Datasource Z-A',
}

/*
 * Add queries to rich history. Save only queries within the retention period, or that are starred.
 * Side-effect: store history in local storage
 */

export const MAX_HISTORY_ITEMS = 10000;

export async function addToRichHistory(
  richHistory: RichHistoryQuery[],
  datasourceId: string,
  datasourceName: string | null,
  queries: DataQuery[],
  starred: boolean,
  comment: string | null,
  sessionName: string,
  showQuotaExceededError: boolean,
  showLimitExceededWarning: boolean
): Promise<{ richHistory: RichHistoryQuery[]; localStorageFull?: boolean; limitExceeded?: boolean }> {
  const ts = Date.now();
  /* Save only queries, that are not falsy (e.g. empty object, null, ...) */
  const newQueriesToSave: DataQuery[] = queries && queries.filter((query) => notEmptyQuery(query));

  const queriesToKeep = await getRichHistoryService().purgeQueries(richHistory);
  if (newQueriesToSave.length > 0) {
    /* Compare queries of a new query and last saved queries. If they are the same, (except selected properties,
     * which can be different) don't save it in rich history.
     */
    const newQueriesToCompare = newQueriesToSave.map((q) => omit(q, ['key', 'refId']));
    const lastQueriesToCompare =
      queriesToKeep.length > 0 &&
      queriesToKeep[0].queries.map((q) => {
        return omit(q, ['key', 'refId']);
      });

    if (isEqual(newQueriesToCompare, lastQueriesToCompare)) {
      return { richHistory };
    }

    let limitExceeded = false;
    try {
      await getRichHistoryService().checkLimits(queriesToKeep);
    } catch (error) {
      limitExceeded = true;
      showLimitExceededWarning && dispatch(notifyApp(createWarningNotification(error.message)));
    }

    let localStorageFull = false;

    let newRichHistory: RichHistoryQuery = {
      queries: newQueriesToSave,
      ts,
      datasourceId,
      datasourceName: datasourceName ?? '',
      starred,
      comment: comment ?? '',
      sessionName,
    };

    let updatedHistory: RichHistoryQuery[] = [newRichHistory, ...queriesToKeep];

    try {
      await getRichHistoryService().addToRichHistory(newRichHistory, queriesToKeep);
    } catch (error) {
      if (error.name === 'StorageFull') {
        localStorageFull = true;
        showQuotaExceededError && dispatch(notifyApp(createErrorNotification(error.message)));
      } else {
        dispatch(notifyApp(createErrorNotification(error.message)));
      }
    }
    return { richHistory: updatedHistory, localStorageFull, limitExceeded };
  }

  return { richHistory };
}

export async function getRichHistory(): Promise<RichHistoryQuery[]> {
  return await getRichHistoryService().getRichHistory();
}

export async function deleteAllFromRichHistory(): Promise<void> {
  return getRichHistoryService().deleteAll();
}

export function updateStarredInRichHistory(richHistory: RichHistoryQuery[], ts: number) {
  const updatedHistory = richHistory.map((query) => {
    /* Timestamps are currently unique - we can use them to identify specific queries */
    if (query.ts === ts) {
      const isStarred = query.starred;
      const updatedQuery = Object.assign({}, query, { starred: !isStarred });
      return updatedQuery;
    }
    return query;
  });

  try {
    store.setObject(RICH_HISTORY_KEY, updatedHistory);
    return updatedHistory;
  } catch (error) {
    dispatch(notifyApp(createErrorNotification('Saving rich history failed', error.message)));
    return richHistory;
  }
}

export function updateCommentInRichHistory(
  richHistory: RichHistoryQuery[],
  ts: number,
  newComment: string | undefined
) {
  const updatedHistory = richHistory.map((query) => {
    if (query.ts === ts) {
      const updatedQuery = Object.assign({}, query, { comment: newComment });
      return updatedQuery;
    }
    return query;
  });

  try {
    store.setObject(RICH_HISTORY_KEY, updatedHistory);
    return updatedHistory;
  } catch (error) {
    dispatch(notifyApp(createErrorNotification('Saving rich history failed', error.message)));
    return richHistory;
  }
}

export async function deleteQueryInRichHistory(
  richHistory: RichHistoryQuery[],
  ts: number
): Promise<RichHistoryQuery[]> {
  const updatedHistory = richHistory.filter((query) => query.ts !== ts);
  try {
    await getRichHistoryService().deleteRichHistory(ts, updatedHistory);
    return updatedHistory;
  } catch (error) {
    dispatch(notifyApp(createErrorNotification('Saving rich history failed', error.message)));
    return richHistory;
  }
}

export const sortQueries = (array: RichHistoryQuery[], sortOrder: SortOrder) => {
  let sortFunc;

  if (sortOrder === SortOrder.Ascending) {
    sortFunc = (a: RichHistoryQuery, b: RichHistoryQuery) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0);
  }
  if (sortOrder === SortOrder.Descending) {
    sortFunc = (a: RichHistoryQuery, b: RichHistoryQuery) => (a.ts < b.ts ? 1 : a.ts > b.ts ? -1 : 0);
  }

  if (sortOrder === SortOrder.DatasourceZA) {
    sortFunc = (a: RichHistoryQuery, b: RichHistoryQuery) =>
      a.datasourceName < b.datasourceName ? -1 : a.datasourceName > b.datasourceName ? 1 : 0;
  }

  if (sortOrder === SortOrder.DatasourceAZ) {
    sortFunc = (a: RichHistoryQuery, b: RichHistoryQuery) =>
      a.datasourceName < b.datasourceName ? 1 : a.datasourceName > b.datasourceName ? -1 : 0;
  }

  return array.sort(sortFunc);
};

export const createUrlFromRichHistory = (query: RichHistoryQuery) => {
  const exploreState: ExploreUrlState = {
    /* Default range, as we are not saving timerange in rich history */
    range: { from: 'now-1h', to: 'now' },
    datasource: query.datasourceName,
    queries: query.queries,
    context: 'explore',
  };

  const serializedState = serializeStateToUrlParam(exploreState, true);
  const baseUrl = /.*(?=\/explore)/.exec(`${window.location.href}`)![0];
  const url = urlUtil.renderUrl(`${baseUrl}/explore`, { left: serializedState });
  return url;
};

/* Needed for slider in Rich history to map numerical values to meaningful strings */
export const mapNumbertoTimeInSlider = (num: number) => {
  let str;
  switch (num) {
    case 0:
      str = 'today';
      break;
    case 1:
      str = 'yesterday';
      break;
    case 7:
      str = 'a week ago';
      break;
    case 14:
      str = 'two weeks ago';
      break;
    default:
      str = `${num} days ago`;
  }

  return str;
};

export const createRetentionPeriodBoundary = (days: number, isLastTs: boolean) => {
  const today = new Date();
  const date = new Date(today.setDate(today.getDate() - days));
  /*
   * As a retention period boundaries, we consider:
   * - The last timestamp equals to the 24:00 of the last day of retention
   * - The first timestamp that equals to the 00:00 of the first day of retention
   */
  const boundary = isLastTs ? date.setHours(24, 0, 0, 0) : date.setHours(0, 0, 0, 0);
  return boundary;
};

export function createDateStringFromTs(ts: number) {
  return dateTimeFormat(ts, {
    format: 'MMMM D',
  });
}

export function getQueryDisplayText(query: DataQuery): string {
  /* If datasource doesn't have getQueryDisplayText, create query display text by
   * stringifying query that was stripped of key, refId and datasource for nicer
   * formatting and improved readability
   */
  const strippedQuery = omit(query, ['key', 'refId', 'datasource']);
  return JSON.stringify(strippedQuery);
}

export function createQueryHeading(query: RichHistoryQuery, sortOrder: SortOrder) {
  let heading = '';
  if (sortOrder === SortOrder.DatasourceAZ || sortOrder === SortOrder.DatasourceZA) {
    heading = query.datasourceName;
  } else {
    heading = createDateStringFromTs(query.ts);
  }
  return heading;
}

export function createQueryText(query: DataQuery, queryDsInstance: DataSourceApi | undefined) {
  /* query DatasourceInstance is necessary because we use its getQueryDisplayText method
   * to format query text
   */
  if (queryDsInstance?.getQueryDisplayText) {
    return queryDsInstance.getQueryDisplayText(query);
  }

  return getQueryDisplayText(query);
}

export function mapQueriesToHeadings(query: RichHistoryQuery[], sortOrder: SortOrder) {
  let mappedQueriesToHeadings: any = {};

  query.forEach((q) => {
    let heading = createQueryHeading(q, sortOrder);
    if (!(heading in mappedQueriesToHeadings)) {
      mappedQueriesToHeadings[heading] = [q];
    } else {
      mappedQueriesToHeadings[heading] = [...mappedQueriesToHeadings[heading], q];
    }
  });

  return mappedQueriesToHeadings;
}

/* Create datasource list with images. If specific datasource retrieved from Rich history is not part of
 * exploreDatasources add generic datasource image and add property isRemoved = true.
 */
export function createDatasourcesList(queriesDatasources: string[]) {
  const datasources: Array<{ label: string; value: string; imgUrl: string; isRemoved: boolean }> = [];

  queriesDatasources.forEach((dsName) => {
    const dsSettings = getDataSourceSrv().getInstanceSettings(dsName);
    if (dsSettings) {
      datasources.push({
        label: dsSettings.name,
        value: dsSettings.name,
        imgUrl: dsSettings.meta.info.logos.small,
        isRemoved: false,
      });
    } else {
      datasources.push({
        label: dsName,
        value: dsName,
        imgUrl: 'public/img/icn-datasource.svg',
        isRemoved: true,
      });
    }
  });
  return datasources;
}

export function notEmptyQuery(query: DataQuery) {
  /* Check if query has any other properties besides key, refId and datasource.
   * If not, then we consider it empty query.
   */
  const strippedQuery = omit(query, ['key', 'refId', 'datasource']);
  const queryKeys = Object.keys(strippedQuery);

  if (queryKeys.length > 0) {
    return true;
  }

  return false;
}

export function filterQueriesBySearchFilter(queries: RichHistoryQuery[], searchFilter: string) {
  return queries.filter((query) => {
    if (query.comment.includes(searchFilter)) {
      return true;
    }

    const listOfMatchingQueries = query.queries.filter((query) =>
      // Remove fields in which we don't want to be searching
      Object.values(omit(query, ['datasource', 'key', 'refId', 'hide', 'queryType'])).some((value: any) =>
        value?.toString().includes(searchFilter)
      )
    );

    return listOfMatchingQueries.length > 0;
  });
}

export function filterQueriesByDataSource(queries: RichHistoryQuery[], listOfDatasourceFilters: string[]) {
  return listOfDatasourceFilters && listOfDatasourceFilters.length > 0
    ? queries.filter((q) => listOfDatasourceFilters.includes(q.datasourceName))
    : queries;
}

export function filterQueriesByTime(queries: RichHistoryQuery[], timeFilter: [number, number]) {
  return queries.filter(
    (q) =>
      q.ts < createRetentionPeriodBoundary(timeFilter[0], true) &&
      q.ts > createRetentionPeriodBoundary(timeFilter[1], false)
  );
}

export function filterAndSortQueries(
  queries: RichHistoryQuery[],
  sortOrder: SortOrder,
  listOfDatasourceFilters: string[],
  searchFilter: string,
  timeFilter?: [number, number]
) {
  const filteredQueriesByDs = filterQueriesByDataSource(queries, listOfDatasourceFilters);
  const filteredQueriesByDsAndSearchFilter = filterQueriesBySearchFilter(filteredQueriesByDs, searchFilter);
  const filteredQueriesToBeSorted = timeFilter
    ? filterQueriesByTime(filteredQueriesByDsAndSearchFilter, timeFilter)
    : filteredQueriesByDsAndSearchFilter;

  return sortQueries(filteredQueriesToBeSorted, sortOrder);
}
