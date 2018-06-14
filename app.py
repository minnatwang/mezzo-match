from flask import Flask, render_template, request, current_app, send_from_directory
import os
import numpy as np
import pandas as pd
import sys

app = Flask(__name__)

UPLOAD_FOLDER = os.path.basename('uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

tie_break = 0
max_tie_break = 100
original_filename = ''
groups = []


def get_requests_from_data(df):
    df = df.replace(np.nan, '', regex=True)

    selections = [s for s in list(df) if '_' in s]

    col_names = ['entity'] + selections + ['type']
    df_requests = df[col_names].copy()

    # Delete duplicate requests (one entity requesting the same other entity multiple times)
    def delete_duplicate_requests(r):
        to_overwrite = r[r.duplicated()].keys().tolist()
        r[to_overwrite] = ''
        return r

    df_requests.apply(delete_duplicate_requests, axis=1)

    main_choices_indices = [i for i, s in enumerate(list(df_requests)) if 'choice_' in s]
    backup_choices_indices = [i for i, s in enumerate(list(df_requests)) if 'backup_' in s]

    # Flatten so each request is its own row
    requests = []
    for _, row in df_requests.iterrows():
        for col in main_choices_indices + backup_choices_indices:
            # print(row)
            # print(col)
            reqd = row[col]
            # print(reqd)
            if reqd == '':
                continue
            try:
                reqd_type = df[df['entity'] == reqd].iloc[0]['type']
            except IndexError:
                print(f'The entity %{reqd} does not exist in the spreadsheet, so it\'s being skipped.')
                continue
            multiplier = 1.0 if col in main_choices_indices else 0.5  # change 0.5 to 0.25?
            requests.append([row['entity'], reqd, multiplier, row['type'], reqd_type])

    col_names = ['requester', 'requested', 'multiplier', 'reqr_type', 'reqd_type']
    df_request_pairs = pd.DataFrame(requests, columns=col_names)

    def calculate_choice_score(row_ind):
        """Calculate score for each request and add it as a new column"""
        requester = df_request_pairs.at[row_ind.name, 'requester']
        requested = df_request_pairs.at[row_ind.name, 'requested']
        importance_requester = df[df['entity'] == requester].iloc[0]['importance']
        importance_requested = df[df['entity'] == requested].iloc[0]['importance']

        # print(f'{requester} x {requested} score is {importance_requested * importance_requester}')
        return df_request_pairs.at[row_ind.name, 'multiplier'] * importance_requester * importance_requested

    df_request_pairs['score'] = df_request_pairs.apply(calculate_choice_score, axis=1)

    return df_request_pairs, df_requests, len(main_choices_indices)


def clean_up_requests(df_request_pairs):
    # Delete any company-company and investor-investor meetings
    df_request_pairs['type_total'] = df_request_pairs['reqr_type'] + df_request_pairs['reqd_type']
    df_request_pairs = df_request_pairs[df_request_pairs['type_total'] == 1]
    # print(f'Deleted all same-type meetings:\n{df_request_pairs.head()}')

    # Take care of duplicate meetings (add scores if both ppl wanted to meet)
    df_requests_co_first = df_request_pairs[df_request_pairs['reqr_type'] == 0]
    # NOTE: this means all meetings in the requests df MUST be actual requests. Can't add in all possibilities.
    df_requests_co_first = df_requests_co_first.assign(co_req=True)  # doesn't differentiate backup choices
    df_requests_inv_first = df_request_pairs[df_request_pairs['reqr_type'] == 1]
    df_requests_inv_first = df_requests_inv_first.assign(co_req=False)
    # print(f'Make sure sizes match: {df_requests_co_first.shape[0] + df_requests_inv_first.shape[0]} '
    #       f'= {df_request_pairs.shape[0]}')
    if (df_requests_co_first.shape[0] + df_requests_inv_first.shape[0]) != df_request_pairs.shape[0]:
        print('Error in code - investor + company requests do not match for final dataframe')
        sys.exit()

    # Reverse all inv_first
    df_requests_inv_first.rename(columns={'requested': 'requester', 'requester': 'requested'}, inplace=True)
    df_requests_inv_first.loc[:, ('reqr_type', 'reqd_type')] = [0, 1]
    # combine again
    df_requests_combined = df_requests_co_first.append(df_requests_inv_first)
    # Add scores together; NOTE: this is also summing reqr_type type_total, reqd_type, multiplier cols
    df_requests_combined = df_requests_combined.groupby(['requested', 'requester']).sum().reset_index()

    # Sort input matrix by score.
    df_requests_combined_sorted = df_requests_combined.sort_values(by=['score'], ascending=False)
    df_requests_combined_sorted.rename(columns={'requested': 'entity2', 'requester': 'entity1'}, inplace=True)
    df_requests_combined_sorted = df_requests_combined_sorted.assign(scheduled=False)
    # print(df_requests_combined_sorted.head())
    # Reorder everything based on our preferences (e.g. if two meetings have the same score)

    return df_requests_combined_sorted


def create_schedule(df, num_meetings):
    df_schedule = df[['entity']].copy()
    df_schedule = df_schedule.drop_duplicates(subset=['entity'])

    for ind in range(1, num_meetings + 1):
        df_schedule['mtg' + str(ind)] = ''
        # mtg#_req represents if that person requested that meeting or not
        df_schedule['mtg' + str(ind) + '_req'] = False

    return df_schedule


def schedule_over_unavailability(df_schedule, entity, conflicts_str):
    # print('Let\'s block out times when attendees are unavailable!')
    more = True
    while more:
        if entity == 'DONE':
            # more = False
            break

        # Catch error where entity name doesn't match
        try:
            entity_row = df_schedule.index[df_schedule['entity'] == entity].tolist()[0]
        except IndexError:
            print('That entity name doesn\'t match with anything in your spreadsheet. '
                  'Please check the name and remember that spaces matter!')
            continue

        # conflicts_str = input(f"Which meetings can't {entity} make? "
        #                       f"(format should be \'#\' or \'#,#\'; "
        #                       f"e.g. if they can't make meetings 1 and 3, enter \'1,3\'\n")
        conflicts = map(int, conflicts_str.split(','))

        try:
            for conflict in conflicts:
                index2 = df_schedule.columns.get_loc('mtg' + str(conflict))
                df_schedule.iloc[entity_row, index2] = "N/A"
        except ValueError:
            print(f'Whoops! I can\'t process {conflicts}, might\'ve been invalid formatting. Let\'s try that again.')
            continue
        except KeyError:
            print(f'Whoops! Looks like one or more of the meetings you entered ({conflicts}) '
                  f'doesn\'t exist according to your csv. Let\'s try that again.')
            continue

    return df_schedule


def fill_schedule_old(df_schedule, df_requests_combined_sorted, df_requests, df):
    # includes tie-breaking
    grouped_by_score = df_requests_combined_sorted.groupby('score', sort=False)

    for name, group in grouped_by_score:
        # pull out all matches (any non matches are moved up to the top of the list)
        duplicates1 = group.duplicated(subset='entity1', keep=False).tolist()
        duplicates2 = group.duplicated(subset='entity2', keep=False).tolist()
        duplicates_boolean = [a or b for a, b in zip(duplicates1, duplicates2)]
        singles_boolean = [not i for i in duplicates_boolean]

        duplicates_inds = [i for (i, v) in zip(group.index.values.tolist(), duplicates_boolean) if v]
        singles_inds = [i for (i, v) in zip(group.index.values.tolist(), singles_boolean) if v]

        # for those, delete if either entity1 or entity2 has no free columns
        for ind in duplicates_inds:
            entity1 = group.loc[ind]['entity1']
            entity2 = group.loc[ind]['entity2']
            entity1_row = df_schedule.index[df['entity'] == entity1].tolist()[0]
            entity2_row = df_schedule.index[df['entity'] == entity2].tolist()[0]
            entity1_open_cols = [i for i, x in enumerate(df_schedule.iloc[entity1_row] == '') if x]
            entity2_open_cols = [i for i, x in enumerate(df_schedule.iloc[entity2_row] == '') if x]

            if len(entity1_open_cols) == 0 | len(entity2_open_cols) == 0:
                duplicates_inds.remove(ind)

        # do a check to get rid of singles within duplicates_inds (caused by deleting those with no free columns)
        duplicates1_2 = group.loc[duplicates_inds, :].duplicated(subset='entity1', keep=False).tolist()
        duplicates2_2 = group.loc[duplicates_inds, :].duplicated(subset='entity2', keep=False).tolist()
        duplicates_boolean_2 = [a or b for a, b in zip(duplicates1_2, duplicates2_2)]
        duplicates_inds = [i for (i, v) in zip(group.index.values.tolist(), duplicates_boolean_2) if v]

        # ask for new order
        if len(duplicates_inds) > 0:
            print('-----------------------------------------------')
            print(group.loc[duplicates_inds, ['entity2', 'entity1']])
            var = input(
                "I need your help breaking ties! Reorder the above meeting pairs with most important mtgs "
                "first using the row index (the first number in each row). (format: \'1,2,3\' or \'1\' or "
                "\'1,3\') Or type \'SAME\' if it's already in the order you want. \n")

            # todo try to find a way to do a separated sort by entity involved? how to order that?

            try:
                if var == 'SAME':
                    new_order_dupl = duplicates_inds
                else:
                    # Change order in the original df
                    new_order_dupl = [int(s) for s in var.split(',')]  # .strip('[]')
            except ValueError:
                print(f'Oops, looks like there are some formatting errors in what you typed ({var}). '
                      f'I\'m going to exit so we can start over.')
                sys.exit()
        else:
            new_order_dupl = duplicates_inds

        new_order = singles_inds + new_order_dupl
        group = group.reindex(new_order)

        # in order of matrix, schedule meetings.
        for ind, row in group.iterrows():
            # print(ind)
            entity1 = row['entity1']  # all companies are entity1; inv are entity2
            entity2 = row['entity2']
            try:
                entity1_row = df_schedule.index[df['entity'] == entity1].tolist()[0]
                entity2_row = df_schedule.index[df['entity'] == entity2].tolist()[0]
            except IndexError:
                print(f'Oops, looks like one or more of the numbers in {var} might be wrong.')
                sys.exit()
            entity1_open_cols = [i for i, x in enumerate(df_schedule.iloc[entity1_row] == '') if x]
            entity2_open_cols = [i for i, x in enumerate(df_schedule.iloc[entity2_row] == '') if x]

            try:
                # open_col = set(entity1_open_cols).intersection(entity2_open_cols).pop()
                # print(df_schedule.iloc[entity1_row])
                # print(df_schedule.iloc[entity2_row])
                open_col = min(set(entity1_open_cols).intersection(entity2_open_cols))

            except AttributeError:
                continue

            except ValueError:
                continue

            df_schedule.iloc[entity1_row, open_col] = entity2
            df_schedule.iloc[entity2_row, open_col] = entity1
            df_requests_combined_sorted.loc[ind, 'scheduled'] = True

            # Mark if the entity requested that meeting; NOTE: includes backups
            entity1_requests = df_requests[df_requests['entity'] == entity1].values
            df_schedule.iloc[entity1_row, open_col + 1] = entity2 in entity1_requests
            entity2_requests = df_requests[df_requests['entity'] == entity2].values
            df_schedule.iloc[entity2_row, open_col + 1] = entity1 in entity2_requests

    return df_schedule, df_requests_combined_sorted


def offer_reorder(df_schedule, df_requests_combined_sorted, df):
    global tie_break
    global groups
    global tie_break
    global max_tie_break
    # includes tie-breaking

    if tie_break == 0:
        grouped_by_score = df_requests_combined_sorted.groupby('score', sort=False)
        groups = [name for name, dfs in grouped_by_score]
        max_tie_break = len(groups)

    print('groups:')
    print(groups)

    group = grouped_by_score.get_group(groups[tie_break])

    # pull out all matches (any non matches are moved up to the top of the list)
    duplicates1 = group.duplicated(subset='entity1', keep=False).tolist()
    duplicates2 = group.duplicated(subset='entity2', keep=False).tolist()
    duplicates_boolean = [a or b for a, b in zip(duplicates1, duplicates2)]

    duplicates_inds = [i for (i, v) in zip(group.index.values.tolist(), duplicates_boolean) if v]
    print(duplicates_inds)

    # for those, delete if either entity1 or entity2 has no free columns
    for ind in duplicates_inds:
        entity1 = group.loc[ind]['entity1']
        entity2 = group.loc[ind]['entity2']
        entity1_row = df_schedule.index[df['entity'] == entity1].tolist()[0]
        entity2_row = df_schedule.index[df['entity'] == entity2].tolist()[0]
        entity1_open_cols = [i for i, x in enumerate(df_schedule.iloc[entity1_row] == '') if x]
        entity2_open_cols = [i for i, x in enumerate(df_schedule.iloc[entity2_row] == '') if x]
        # print(df_schedule.iloc[entity1_row])
        # print(entity1_open_cols)
        # print(df_schedule.iloc[entity2_row])
        # print(entity2_open_cols)

        if len(entity1_open_cols) == 0 or len(entity2_open_cols) == 0:
            duplicates_inds.remove(ind)
            # print(str(ind) + '\'s schedule is full and will be deleted')
        # delete any companies that can't possibly match
        elif len(set(entity1_open_cols).intersection(entity2_open_cols)) == 0:
            duplicates_inds.remove(ind)
            # print(str(ind) + ' has no common availability and will be deleted')

    # do a check to get rid of singles within duplicates_inds (caused by deleting those with no free columns)
    duplicates1_2 = group.loc[duplicates_inds, :].duplicated(subset='entity1', keep=False).tolist()
    duplicates2_2 = group.loc[duplicates_inds, :].duplicated(subset='entity2', keep=False).tolist()
    # print(duplicates1_2)
    # print(duplicates2_2)
    duplicates_boolean_2 = [a or b for a, b in zip(duplicates1_2, duplicates2_2)]
    duplicates_inds_2 = [i for (i, v) in zip(
        group.loc[duplicates_inds, :].index.values.tolist(), duplicates_boolean_2) if v]
    print(duplicates_inds_2)

    tie_break = tie_break + 1

    # ask for new order
    if len(duplicates_inds_2) > 0:
        # todo try to find a way to do a separated sort by entity involved? how to order that?
        # todo for the future, could also output how many slots each company has or order by company/investor name
        print(str(group.loc[duplicates_inds_2, ['entity2', 'entity1']]))
        return str(group.loc[duplicates_inds_2, ['entity2', 'entity1']]), \
               str(group.loc[duplicates_inds_2].index.values.tolist()).replace(']', '').replace('[', '')

    else:
        return None, None
        # new_order_dupl = duplicates_inds


def fill_schedule(df_schedule, df_requests_combined_sorted, df_requests, df, var):
    global tie_break
    # includes tie-breaking
    # grouped_by_score = df_requests_combined_sorted.groupby('score', sort=True)
    # groups = [name for name, dfs in grouped_by_score]
    print('fill_schedule groups:')
    print(groups)

    group = grouped_by_score.get_group(groups[tie_break - 1])

    duplicates1 = group.duplicated(subset='entity1', keep=False).tolist()
    duplicates2 = group.duplicated(subset='entity2', keep=False).tolist()
    duplicates_boolean = [a or b for a, b in zip(duplicates1, duplicates2)]
    singles_boolean = [not i for i in duplicates_boolean]
    singles_inds = [i for (i, v) in zip(group.index.values.tolist(), singles_boolean) if v]
    duplicates_inds = [i for (i, v) in zip(group.index.values.tolist(), duplicates_boolean) if v]

    try:
        if var is None or var == 'SAME':
            new_order_dupl = duplicates_inds
        else:
            # Change order in the original df
            new_order_dupl = [int(s) for s in var.split(',')]  # .strip('[]')
            print(new_order_dupl)

    except ValueError:
        msg = f'Oops, looks like there are some formatting errors in what you typed ({var}). ' \
              f'I\'m going to exit so we can start over.'
        raise ValueError(msg)

    # Note that any request pairs where one/two parties have no availability or no common availability are deleted
    # This is b/c reindexing deletes any indices not mentioned
    new_order = singles_inds + new_order_dupl
    # print('The new order is:')
    # print(new_order)
    print('The old group is:')
    print(group)
    group = group.reindex(new_order)
    print('The new group is:')
    print(group)

    # in order of matrix, schedule meetings.
    for ind, row in group.iterrows():
        # print(ind)
        entity1 = row['entity1']  # all companies are entity1; inv are entity2
        entity2 = row['entity2']
        try:
            entity1_row = df_schedule.index[df['entity'] == entity1].tolist()[0]
            entity2_row = df_schedule.index[df['entity'] == entity2].tolist()[0]
        except IndexError as err:
            msg = f'Oops, looks like one or more of the numbers in {var} might be wrong. '
            raise ValueError(str(err) + '\n' + msg)

        entity1_open_cols = [i for i, x in enumerate(df_schedule.iloc[entity1_row] == '') if x]
        entity2_open_cols = [i for i, x in enumerate(df_schedule.iloc[entity2_row] == '') if x]

        try:
            # open_col = set(entity1_open_cols).intersection(entity2_open_cols).pop()
            # print(df_schedule.iloc[entity1_row])
            # print(df_schedule.iloc[entity2_row])
            open_col = min(set(entity1_open_cols).intersection(entity2_open_cols))

        except AttributeError:
            continue

        except ValueError:
            continue

        df_schedule.iloc[entity1_row, open_col] = entity2
        df_schedule.iloc[entity2_row, open_col] = entity1
        df_requests_combined_sorted.loc[ind, 'scheduled'] = True

        # Mark if the entity requested that meeting; NOTE: includes backups
        entity1_requests = df_requests[df_requests['entity'] == entity1].values
        df_schedule.iloc[entity1_row, open_col + 1] = entity2 in entity1_requests
        entity2_requests = df_requests[df_requests['entity'] == entity2].values
        df_schedule.iloc[entity2_row, open_col + 1] = entity1 in entity2_requests

    return df_schedule, df_requests_combined_sorted


def check_column_names(df):
    if not ('entity' in df.columns):
        msg = 'There is no column labeled \'entity\'. Please fix your spreadsheet and try again.'
        raise ValueError(msg)
        # sys.exit()

    if not ('type' in df.columns):
        msg = 'There is no column labeled \'type\'. Please fix your spreadsheet and try again.'
        raise ValueError(msg)

    if not ('importance' in df.columns):
        msg = 'There is no column labeled \'importance\'. Please fix your spreadsheet and try again.'
        raise ValueError(msg)

    if sum([1 for col in df.columns if 'choice' in col]) == 0:
        msg = 'There are no meeting request columns (should be in the format \'choice_#\'). ' \
              'Please fix your spreadsheet and try again.'
        raise ValueError(msg)


@app.route("/")
def index():
    return render_template("upload.html")


@app.route('/uploads/<path:filename>', methods=['GET', 'POST'])
def download(filename):
    uploads = os.path.join(current_app.root_path, app.config['UPLOAD_FOLDER'])
    return send_from_directory(directory=uploads, filename=filename)


@app.route("/upload", methods=['POST'])
def upload():
    global original_filename
    msg = None
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])

    if request.method == "POST":
        file = request.files['file']
        # print(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(filepath)
        # print(filepath)
        original_filename = filepath

        try:
            print('STATUS: Reading file')
            df = pd.read_csv(filepath)

            print('STATUS: Checking column names\n')
            check_column_names(df)

            print('STATUS: Taking in data\n')
            df_request_pairs, df_requests, num_meetings = get_requests_from_data(df)
            df_requests_combined_sorted = clean_up_requests(df_request_pairs)

            print('STATUS: Setting up schedule\n')
            df_schedule = create_schedule(df, num_meetings)

        except Exception as err:
            msg = 'Something went wrong. ' + \
                  '\nError: ' + str(err) + \
                  '\n\n Please check your spreadsheet and try again or contact Minna.'

        else:
            df.to_csv(os.path.join(app.config['UPLOAD_FOLDER'], 'df') + '.csv')
            df_requests.to_csv(os.path.join(app.config['UPLOAD_FOLDER'], 'df_requests') + '.csv')
            df_schedule.to_csv(os.path.join(app.config['UPLOAD_FOLDER'], 'df_schedule') + '.csv')
            df_requests_combined_sorted.to_csv(os.path.join(
                app.config['UPLOAD_FOLDER'], 'df_requests_combined_sorted') + '.csv')

    schedule_link = os.path.join(app.config['UPLOAD_FOLDER'], 'df_schedule') + '.csv'
    # schedule_link = os.path.join(current_app.root_path, app.config['UPLOAD_FOLDER'], 'df_schedule') + '.csv'

    return render_template("schedule_unavailability.html", msg=msg, schedule_link=schedule_link)


@app.route("/schedule_unavailability", methods=['POST'])
def schedule_unavailability():
    global tie_break
    ties_to_break = None
    msg = None
    filepath = None
    ties_to_break_indices = None

    if request.method == "POST":

        # entity_list = request.form["entities"].split("\n")
        # conflict_strs_list = request.form["conflict_strs"].split("\n")
        # print(entity_list)

        try:

            file = request.files['file']
            filename = file.filename
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            os.remove(filepath)
            file.save(filepath)

        except Exception as err:
            msg = 'Something went wrong. ' + '\nError: ' + str(
                err) + '\n\n Please check your spreadsheet and try again or contact Minna.'

            return render_template("break_ties.html", msg=msg)

    df_schedule = pd.read_csv(filepath, index_col=0)
    df_schedule = df_schedule.replace(np.nan, '', regex=True)

    df_requests_combined_sorted = pd.read_csv(os.path.join(
        app.config['UPLOAD_FOLDER'], 'df_requests_combined_sorted') + '.csv', index_col=0)
    df_requests = pd.read_csv(
        os.path.join(app.config['UPLOAD_FOLDER'], 'df_requests') + '.csv')
    df = pd.read_csv(
        os.path.join(app.config['UPLOAD_FOLDER'], 'df') + '.csv')
    # print(df_schedule)

    try:
        print('STATUS: Scheduling (with tie breaks)\n')
        ties_to_break, ties_to_break_indices = offer_reorder(df_schedule, df_requests_combined_sorted, df)
        print('max_tie_break = ' + str(max_tie_break))
        print('tie_break = ' + str(tie_break))

        while ties_to_break is None:
            df_schedule, df_requests_combined_sorted = fill_schedule(
                df_schedule, df_requests_combined_sorted, df_requests, df, ties_to_break)
            ties_to_break, ties_to_break_indices = offer_reorder(df_schedule, df_requests_combined_sorted, df)
            print('tie_break = ' + str(tie_break))
            print(df_schedule)

            if tie_break == max_tie_break:
                schedule_link = os.path.join(app.config['UPLOAD_FOLDER'], 'df_schedule') + '.csv'
                requests_link = os.path.join(app.config['UPLOAD_FOLDER'], 'df_requests_combined_sorted') + '.csv'

                return render_template("download_schedule.html",
                                       schedule_link=schedule_link, requests_link=requests_link)
    except ValueError as err:
        msg = 'Something went wrong. ' + \
              '\nError: ' + str(err) + \
              '\n\n Please check your submission and try again or contact Minna.'

    df_schedule.to_csv(os.path.join(app.config['UPLOAD_FOLDER'], 'df_schedule') + '.csv')
    df_requests_combined_sorted.to_csv(os.path.join(
        app.config['UPLOAD_FOLDER'], 'df_requests_combined_sorted') + '.csv')

    if tie_break == max_tie_break:
        schedule_link = os.path.join(app.config['UPLOAD_FOLDER'], 'df_schedule') + '.csv'
        requests_link = os.path.join(app.config['UPLOAD_FOLDER'], 'df_requests_combined_sorted') + '.csv'

        return render_template("download_schedule.html", schedule_link=schedule_link, requests_link=requests_link)
    else:
        progress = str(tie_break) + ' out of ' + str(max_tie_break)
        return render_template("break_ties.html", msg=msg, ties_to_break=ties_to_break, progress=progress,
                               ties_to_break_indices=ties_to_break_indices)


@app.route("/break_ties", methods=['POST'])
def break_ties():
    global tie_break
    ties_to_break = None
    msg = None
    ties_to_break_indices = None

    df_schedule = pd.read_csv(os.path.join(app.config['UPLOAD_FOLDER'], 'df_schedule') + '.csv', index_col=0)
    df_schedule = df_schedule.replace(np.nan, '', regex=True)

    df_requests_combined_sorted = pd.read_csv(
        os.path.join(app.config['UPLOAD_FOLDER'], 'df_requests_combined_sorted') + '.csv', index_col=0)
    df_requests = pd.read_csv(
        os.path.join(app.config['UPLOAD_FOLDER'], 'df_requests') + '.csv')
    df = pd.read_csv(
        os.path.join(app.config['UPLOAD_FOLDER'], 'df') + '.csv')

    if request.method == "POST":
        broken_tie_indices = request.form["order"]

        try:
            df_schedule, df_requests_combined_sorted = fill_schedule(df_schedule, df_requests_combined_sorted,
                                                                     df_requests, df, broken_tie_indices)
            ties_to_break, ties_to_break_indices = offer_reorder(df_schedule, df_requests_combined_sorted, df)
            print('tie_break = ' + str(tie_break))
            print(df_schedule)
        except ValueError as err:
            msg = 'Something went wrong. ' + \
                  '\nError: ' + str(err) + \
                  '\n\n Please press the back button, check your submission, fix it, and resubmit it.'

        try:
            while ties_to_break is None:
                df_schedule, df_requests_combined_sorted = fill_schedule(df_schedule, df_requests_combined_sorted,
                                                                         df_requests, df, ties_to_break)
                ties_to_break, ties_to_break_indices = offer_reorder(df_schedule, df_requests_combined_sorted, df)
                print('tie_break = ' + str(tie_break))

                if tie_break == max_tie_break:
                    schedule_link = os.path.join(app.config['UPLOAD_FOLDER'], 'df_schedule') + '.csv'
                    requests_link = os.path.join(app.config['UPLOAD_FOLDER'], 'df_requests_combined_sorted') + '.csv'

                    return render_template("download_schedule.html", schedule_link=schedule_link,
                                           requests_link=requests_link)

            df_schedule.to_csv(os.path.join(app.config['UPLOAD_FOLDER'], 'df_schedule') + '.csv')
            df_requests_combined_sorted.to_csv(
                os.path.join(app.config['UPLOAD_FOLDER'], 'df_requests_combined_sorted') + '.csv')

        except ValueError as err:
            msg = 'Something went wrong internally. ' + \
                  '\nError: ' + str(err) + \
                  '\n\n Please check your submission and try again or contact Minna.'

    if tie_break == max_tie_break:
        schedule_link = os.path.join(app.config['UPLOAD_FOLDER'], 'df_schedule') + '.csv'
        requests_link = os.path.join(app.config['UPLOAD_FOLDER'], 'df_requests_combined_sorted') + '.csv'

        return render_template("download_schedule.html", schedule_link=schedule_link, requests_link=requests_link)
    else:
        progress = str(tie_break) + ' out of ' + str(max_tie_break)
        return render_template("break_ties.html", msg=msg, ties_to_break=ties_to_break, progress=progress,
                               ties_to_break_indices=ties_to_break_indices)


@app.route("/download_schedule", methods=['POST'])
def download_schedule():
    # delete the folder
    # os.remove(os.path.join(app.config['UPLOAD_FOLDER'], 'df_schedule') + '.csv')
    # os.remove(os.path.join(app.config['UPLOAD_FOLDER'], 'df') + '.csv')
    # os.remove(os.path.join(app.config['UPLOAD_FOLDER'], 'df_requests_combined_sorted') + '.csv')
    # os.remove(os.path.join(app.config['UPLOAD_FOLDER'], 'df_requests') + '.csv')
    # os.remove(original_filename)

    return render_template("completed.html")


if __name__ == "__main__":
    app.run(debug=True)
