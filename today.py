import datetime
from dateutil import relativedelta
import requests
import os
from lxml import etree
import time

HEADERS = {'authorization': 'token ' + os.environ['ACCESS_TOKEN']}
USER_NAME = os.environ.get('USER_NAME', 'dinoarla')
QUERY_COUNT = {
    'user_getter': 0, 'follower_getter': 0,
    'graph_repos_stars': 0, 'recursive_loc': 0,
    'graph_commits': 0, 'loc_query': 0,
}


# ── helpers ──────────────────────────────────────────────────────────────────

def format_plural(unit):
    return 's' if unit != 1 else ''


def daily_readme(start_date):
    """Return elapsed time since start_date as 'X years, Y months, Z days'."""
    diff = relativedelta.relativedelta(datetime.datetime.utcnow(), start_date)
    return '{} {}, {} {}, {} {}'.format(
        diff.years,  'year'  + format_plural(diff.years),
        diff.months, 'month' + format_plural(diff.months),
        diff.days,   'day'   + format_plural(diff.days),
    )


def simple_request(func_name, query, variables):
    r = requests.post(
        'https://api.github.com/graphql',
        json={'query': query, 'variables': variables},
        headers=HEADERS,
    )
    if r.status_code == 200:
        return r
    raise Exception(func_name, 'failed with', r.status_code, r.text)


def query_count(funct_id):
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


def perf_counter(funct, *args):
    start = time.perf_counter()
    result = funct(*args)
    return result, time.perf_counter() - start


def formatter(label, diff):
    print(f'   {label:<20}', end='')
    if diff > 1:
        print(f'{diff:>10.4f} s')
    else:
        print(f'{diff*1000:>8.4f} ms')


# ── GitHub API queries ────────────────────────────────────────────────────────

def user_getter(username):
    query_count('user_getter')
    query = '''
    query($login: String!) {
        user(login: $login) { id createdAt }
    }'''
    r = simple_request('user_getter', query, {'login': username})
    data = r.json()['data']['user']
    return {'id': data['id']}, data['createdAt']


def follower_getter(username):
    query_count('follower_getter')
    query = '''
    query($login: String!) {
        user(login: $login) { followers { totalCount } }
    }'''
    r = simple_request('follower_getter', query, {'login': username})
    return int(r.json()['data']['user']['followers']['totalCount'])


def graph_repos_stars(count_type, owner_affiliation, cursor=None):
    query_count('graph_repos_stars')
    query = '''
    query($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges { node { ... on Repository { stargazers { totalCount } } } }
                pageInfo { endCursor hasNextPage }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    r = simple_request('graph_repos_stars', query, variables)
    repos = r.json()['data']['user']['repositories']
    if count_type == 'repos':
        return repos['totalCount']
    if count_type == 'stars':
        total = sum(e['node']['stargazers']['totalCount'] for e in repos['edges'])
        if repos['pageInfo']['hasNextPage']:
            total += graph_repos_stars('stars', owner_affiliation, repos['pageInfo']['endCursor'])
        return total


def graph_commits(start_date, end_date):
    query_count('graph_commits')
    query = '''
    query($start_date: DateTime!, $end_date: DateTime!, $login: String!) {
        user(login: $login) {
            contributionsCollection(from: $start_date, to: $end_date) {
                contributionCalendar { totalContributions }
            }
        }
    }'''
    variables = {'start_date': start_date, 'end_date': end_date, 'login': USER_NAME}
    r = simple_request('graph_commits', query, variables)
    return int(r.json()['data']['user']['contributionsCollection']['contributionCalendar']['totalContributions'])


def commit_counter(acc_date):
    """Sum all contributions from account creation to today (in annual chunks)."""
    total = 0
    start = datetime.datetime.fromisoformat(acc_date.replace('Z', '+00:00'))
    now = datetime.datetime.now(datetime.timezone.utc)
    # Walk year-by-year (GitHub API limit: max 1 year per query)
    while start < now:
        end = min(start + relativedelta.relativedelta(years=1), now)
        total += graph_commits(
            start.strftime('%Y-%m-%dT%H:%M:%SZ'),
            end.strftime('%Y-%m-%dT%H:%M:%SZ'),
        )
        start = end
    return total


def recursive_loc(owner, repo_name, addition_total=0, deletion_total=0, my_commits=0, cursor=None):
    query_count('recursive_loc')
    query = '''
    query($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            totalCount
                            edges {
                                node {
                                    ... on Commit { committedDate }
                                    author { user { id } }
                                    deletions additions
                                }
                            }
                            pageInfo { endCursor hasNextPage }
                        }
                    }
                }
            }
        }
    }'''
    r = requests.post(
        'https://api.github.com/graphql',
        json={'query': query, 'variables': {'repo_name': repo_name, 'owner': owner, 'cursor': cursor}},
        headers=HEADERS,
    )
    if r.status_code != 200:
        raise Exception('recursive_loc failed', r.status_code, r.text)
    ref = r.json()['data']['repository']['defaultBranchRef']
    if ref is None:
        return 0, 0, 0
    history = ref['target']['history']
    for node in history['edges']:
        if node['node']['author']['user'] == OWNER_ID:
            my_commits += 1
            addition_total += node['node']['additions']
            deletion_total += node['node']['deletions']
    if history['edges'] and history['pageInfo']['hasNextPage']:
        return recursive_loc(owner, repo_name, addition_total, deletion_total, my_commits, history['pageInfo']['endCursor'])
    return addition_total, deletion_total, my_commits


def loc_query(owner_affiliation, cursor=None, edges=None):
    query_count('loc_query')
    if edges is None:
        edges = []
    query = '''
    query($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            defaultBranchRef {
                                target {
                                    ... on Commit { history { totalCount } }
                                }
                            }
                        }
                    }
                }
                pageInfo { endCursor hasNextPage }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    r = simple_request('loc_query', query, variables)
    repos_data = r.json()['data']['user']['repositories']
    edges += repos_data['edges']
    if repos_data['pageInfo']['hasNextPage']:
        return loc_query(owner_affiliation, repos_data['pageInfo']['endCursor'], edges)

    add_total = del_total = 0
    for edge in edges:
        repo = edge['node']
        if repo['defaultBranchRef'] is None:
            continue
        owner, name = repo['nameWithOwner'].split('/')
        try:
            a, d, _ = recursive_loc(owner, name)
            add_total += a
            del_total += d
        except Exception:
            pass
    net = add_total - del_total
    return ['{:,}'.format(add_total), '{:,}'.format(del_total), '{:,}'.format(net)]


# ── SVG update ───────────────────────────────────────────────────────────────

def find_and_replace(root, element_id, new_text):
    el = root.find(f".//*[@id='{element_id}']")
    if el is not None:
        el.text = new_text


def justify_format(root, element_id, new_text, length=0):
    """Update element text and adjust preceding dots element for right-justification."""
    if isinstance(new_text, int):
        new_text = f"{new_text:,}"
    new_text = str(new_text)
    find_and_replace(root, element_id, new_text)
    just_len = max(0, length - len(new_text))
    if just_len <= 2:
        dot_map = {0: '', 1: ' ', 2: '. '}
        dot_string = dot_map[just_len]
    else:
        dot_string = ' ' + ('.' * just_len) + ' '
    find_and_replace(root, f'{element_id}_dots', dot_string)


def svg_overwrite(filename, age_data, commit_data, star_data, repo_data, contrib_data, follower_data, loc_data):
    tree = etree.parse(filename)
    root = tree.getroot()
    justify_format(root, 'age_data',      age_data,      49)  # 60 - len('. Uptime:') - 2
    justify_format(root, 'commit_data',   commit_data,   22)
    justify_format(root, 'star_data',     star_data,     14)
    justify_format(root, 'repo_data',     repo_data,      6)
    justify_format(root, 'contrib_data',  contrib_data)
    justify_format(root, 'follower_data', follower_data, 10)
    justify_format(root, 'loc_data',      loc_data[2],    9)
    justify_format(root, 'loc_add',       loc_data[0])
    justify_format(root, 'loc_del',       loc_data[1],    7)
    tree.write(filename, encoding='utf-8', xml_declaration=True)


# ── main ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print('Calculation times:')

    user_data, user_time = perf_counter(user_getter, USER_NAME)
    OWNER_ID, acc_date = user_data
    formatter('account data', user_time)

    # Uptime = time since GitHub account creation
    acc_datetime = datetime.datetime.fromisoformat(acc_date.replace('Z', '+00:00')).replace(tzinfo=None)
    age_data, age_time = perf_counter(daily_readme, acc_datetime)
    formatter('uptime calculation', age_time)

    loc_data, loc_time = perf_counter(loc_query, ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
    formatter('lines of code', loc_time)

    commit_data, commit_time = perf_counter(commit_counter, acc_date)
    formatter('commits', commit_time)

    star_data, star_time = perf_counter(graph_repos_stars, 'stars', ['OWNER'])
    formatter('stars', star_time)

    repo_data, repo_time = perf_counter(graph_repos_stars, 'repos', ['OWNER'])
    formatter('repos', repo_time)

    contrib_data, contrib_time = perf_counter(
        graph_repos_stars, 'repos', ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER']
    )
    formatter('contributed repos', contrib_time)

    follower_data, follower_time = perf_counter(follower_getter, USER_NAME)
    formatter('followers', follower_time)

    svg_overwrite(
        'terminal.svg',
        age_data, commit_data, star_data,
        repo_data, contrib_data, follower_data, loc_data,
    )

    total_time = user_time + age_time + loc_time + commit_time + star_time + repo_time + contrib_time + follower_time
    print(f'\nTotal time: {total_time:.4f} s')
    print(f'GitHub GraphQL API calls: {sum(QUERY_COUNT.values())}')
    for fn, count in QUERY_COUNT.items():
        print(f'   {fn:<28} {count:>4}')
